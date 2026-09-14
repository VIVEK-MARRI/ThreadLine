"""Organisation and membership management router (Stage 24).

Every endpoint here is scoped to the PATH organisation via
``require_org_permission`` — membership and role are evaluated against that
organisation, never against a client-supplied header value.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import (
    Authorisation,
    _org_to_response,
    _user_to_response,
    get_auth_service,
    require_org_permission,
    require_user,
)
from app.auth.models import OrganisationStatus, Permission, Role, User
from app.auth.service import LastOwnerError, NotAMemberError
from app.repositories.auth_repositories import (
    DuplicateOrganisationError,
    DuplicateUserError,
)
from app.schemas.auth import (
    AddMemberRequest,
    ChangeRoleRequest,
    CreateOrganisationRequest,
    MembershipResponse,
    OrganisationMembershipView,
    OrganisationResponse,
    RenameOrganisationRequest,
    UserResponse,
)

router = APIRouter(prefix="/orgs", tags=["Organisations"])


def _membership_view(ctx: Authorisation, membership) -> OrganisationMembershipView:
    return OrganisationMembershipView(
        organisation=_org_to_response(ctx.organisation),
        role=membership.role.value,
        status=membership.status.value,
    )


@router.post(
    "",
    response_model=OrganisationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organisation",
    description="Any authenticated user may create an organisation and becomes its OWNER.",
)
def create_organisation(
    request: CreateOrganisationRequest, user: User = Depends(require_user())
) -> OrganisationResponse:
    try:
        organisation = get_auth_service().create_organisation(
            user.user_id, name=request.name, slug=request.slug
        )
    except DuplicateOrganisationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _org_to_response(organisation)


@router.get(
    "",
    response_model=list[OrganisationMembershipView],
    summary="List my organisations",
    description="Organisations where the caller holds an active membership.",
)
def list_my_organisations(user: User = Depends(require_user())):
    service = get_auth_service()
    views = []
    for membership in service.active_memberships(user.user_id):
        organisation = service._repo.get_organisation(membership.organisation_id)
        if organisation is None or organisation.status != OrganisationStatus.ACTIVE:
            continue
        views.append(
            OrganisationMembershipView(
                organisation=_org_to_response(organisation),
                role=membership.role.value,
                status=membership.status.value,
            )
        )
    return views


@router.get(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    summary="Get an organisation",
    description="Requires membership in the organisation.",
)
def get_organisation(
    ctx: Authorisation = Depends(require_org_permission(Permission.ORG_VIEW)),
) -> OrganisationResponse:
    return _org_to_response(ctx.organisation)


@router.patch(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    summary="Rename an organisation",
    description="OWNER only.",
)
def rename_organisation(
    request: RenameOrganisationRequest,
    ctx: Authorisation = Depends(require_org_permission(Permission.ORG_MANAGE)),
) -> OrganisationResponse:
    from datetime import datetime, timezone

    service = get_auth_service()
    try:
        updated = service._repo.update_organisation(
            ctx.organisation.model_copy(
                update={"name": request.name, "updated_at": datetime.now(timezone.utc)}
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _org_to_response(updated)


def _member_response(ctx: Authorisation, membership) -> MembershipResponse:
    member_user = get_auth_service()._repo.get_user_by_id(membership.user_id)
    return MembershipResponse(
        organisation_id=membership.organisation_id,
        user_id=membership.user_id,
        email=member_user.email if member_user else None,
        role=membership.role.value,
        status=membership.status.value,
    )


@router.get(
    "/{organisation_id}/members",
    response_model=list[MembershipResponse],
    summary="List organisation members",
    description="ADMIN and OWNER only. Reveals member emails within the organisation.",
)
def list_members(
    ctx: Authorisation = Depends(require_org_permission(Permission.MEMBER_MANAGE)),
):
    memberships = get_auth_service()._repo.list_memberships_by_org(
        ctx.organisation_id, active_only=False
    )
    return [_member_response(ctx, m) for m in memberships]


@router.post(
    "/{organisation_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member",
    description=(
        "ADMIN and OWNER. Adds an existing user by email, or creates the user "
        "when a password is supplied. Only an OWNER may grant the OWNER role."
    ),
)
def add_member(
    request: AddMemberRequest,
    ctx: Authorisation = Depends(require_org_permission(Permission.MEMBER_MANAGE)),
) -> MembershipResponse:
    if request.role == Role.OWNER and ctx.role != Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only an OWNER may grant the OWNER role",
        )
    try:
        user, _created = get_auth_service().provision_member(
            organisation_id=ctx.organisation_id,
            email=request.email,
            role=request.role,
            password=request.password,
        )
    except DuplicateUserError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    membership = get_auth_service()._repo.get_membership(ctx.organisation_id, user.user_id)
    return _member_response(ctx, membership)


@router.patch(
    "/{organisation_id}/members/{user_id}",
    response_model=MembershipResponse,
    summary="Change a member role",
    description="ADMIN and OWNER. Only an OWNER may grant OWNER. The last OWNER is protected.",
)
def change_member_role(
    user_id: str,
    request: ChangeRoleRequest,
    ctx: Authorisation = Depends(require_org_permission(Permission.ROLE_MANAGE)),
) -> MembershipResponse:
    try:
        membership = get_auth_service().change_member_role(
            organisation_id=ctx.organisation_id,
            acting_user_id=ctx.user.user_id,
            target_user_id=user_id,
            role=request.role,
        )
    except NotAMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except LastOwnerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _member_response(ctx, membership)


@router.delete(
    "/{organisation_id}/members/{user_id}",
    response_model=MembershipResponse,
    summary="Remove a member",
    description="ADMIN and OWNER. Removal is a tombstone: access ends immediately. The last OWNER is protected.",
)
def remove_member(
    user_id: str,
    ctx: Authorisation = Depends(require_org_permission(Permission.MEMBER_MANAGE)),
) -> MembershipResponse:
    try:
        membership = get_auth_service().remove_member(
            organisation_id=ctx.organisation_id,
            acting_user_id=ctx.user.user_id,
            target_user_id=user_id,
        )
    except NotAMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except LastOwnerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _member_response(ctx, membership)


@router.post(
    "/{organisation_id}/users/{user_id}/disable",
    response_model=UserResponse,
    summary="Disable a user",
    description="OWNER only. Disabled users cannot authenticate anywhere; sessions are revoked.",
)
def disable_user(
    user_id: str,
    ctx: Authorisation = Depends(require_org_permission(Permission.ORG_MANAGE)),
) -> UserResponse:
    try:
        user = get_auth_service().disable_user(
            acting_user_id=ctx.user.user_id, target_user_id=user_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _user_to_response(user)


@router.post(
    "/{organisation_id}/users/{user_id}/enable",
    response_model=UserResponse,
    summary="Re-enable a user",
    description="OWNER only.",
)
def enable_user(
    user_id: str,
    ctx: Authorisation = Depends(require_org_permission(Permission.ORG_MANAGE)),
) -> UserResponse:
    try:
        user = get_auth_service().enable_user(
            acting_user_id=ctx.user.user_id, target_user_id=user_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _user_to_response(user)
