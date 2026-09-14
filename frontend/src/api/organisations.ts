/* Organisation feature API. */

import { request } from "./client";
import type {
  AddMemberRequest,
  MembershipResponse,
  OrganisationMembershipView,
  OrganisationResponse,
} from "../types/auth";

export interface RenameOrganisationRequest {
  name: string;
}

export const organisationsApi = {
  listMine(): Promise<OrganisationMembershipView[]> {
    return request<OrganisationMembershipView[]>("/api/v1/orgs");
  },

  create(payload: { name: string; slug: string }): Promise<OrganisationResponse> {
    return request<OrganisationResponse>("/api/v1/orgs", { method: "POST", body: payload });
  },

  get(organisationId: string): Promise<OrganisationResponse> {
    return request<OrganisationResponse>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}`,
    );
  },

  rename(organisationId: string, payload: RenameOrganisationRequest): Promise<OrganisationResponse> {
    return request<OrganisationResponse>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}`,
      { method: "PATCH", body: payload },
    );
  },

  listMembers(organisationId: string): Promise<MembershipResponse[]> {
    return request<MembershipResponse[]>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}/members`,
    );
  },

  addMember(organisationId: string, payload: AddMemberRequest): Promise<MembershipResponse> {
    return request<MembershipResponse>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}/members`,
      { method: "POST", body: payload },
    );
  },

  changeRole(
    organisationId: string,
    userId: string,
    role: AddMemberRequest["role"],
  ): Promise<MembershipResponse> {
    return request<MembershipResponse>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}/members/${encodeURIComponent(userId)}`,
      { method: "PATCH", body: { role } },
    );
  },

  removeMember(organisationId: string, userId: string): Promise<MembershipResponse> {
    return request<MembershipResponse>(
      `/api/v1/orgs/${encodeURIComponent(organisationId)}/members/${encodeURIComponent(userId)}`,
      { method: "DELETE" },
    );
  },
};
