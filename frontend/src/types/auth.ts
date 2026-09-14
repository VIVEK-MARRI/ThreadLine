/* Auth + organisation contracts. Shapes verified against the backend
 * OpenAPI (`/api/v1/auth/*`, `/api/v1/orgs/*`). */

export type Role = "OWNER" | "ADMIN" | "MEMBER";
export type UserStatus = "ACTIVE" | "DISABLED";
export type OrganisationStatus = "ACTIVE" | "SUSPENDED";
export type MembershipStatus = "ACTIVE" | "REMOVED";

export interface UserResponse {
  user_id: string;
  email: string;
  status: UserStatus;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface OrganisationResponse {
  organisation_id: string;
  name: string;
  slug: string;
  status: OrganisationStatus;
  created_at: string;
}

export interface MembershipResponse {
  organisation_id: string;
  user_id: string;
  email: string | null;
  role: Role;
  status: MembershipStatus;
}

export interface OrganisationMembershipView {
  organisation: OrganisationResponse;
  role: Role;
  status: MembershipStatus;
}

export interface MeResponse {
  user: UserResponse;
  memberships: OrganisationMembershipView[];
}

export interface BootstrapResponse {
  user: UserResponse;
  organisation: OrganisationResponse;
  token: TokenResponse;
}

export interface BootstrapRequest {
  organisation_name: string;
  slug: string;
  admin_email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface CreateOrganisationRequest {
  name: string;
  slug: string;
}

export interface AddMemberRequest {
  email: string;
  role: Role;
  password?: string | null;
}
