/* Auth feature API. Thin typed wrappers over the central client. */

import { request } from "./client";
import type {
  BootstrapRequest,
  BootstrapResponse,
  LoginRequest,
  MeResponse,
  TokenResponse,
  UserResponse,
} from "../types/auth";

export const authApi = {
  bootstrap(payload: BootstrapRequest): Promise<BootstrapResponse> {
    return request<BootstrapResponse>("/api/v1/auth/bootstrap", {
      method: "POST",
      body: payload,
      auth: false,
    });
  },

  login(payload: LoginRequest): Promise<TokenResponse> {
    return request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: payload,
      auth: false,
    });
  },

  logout(): Promise<{ status: string }> {
    return request<{ status: string }>("/api/v1/auth/logout", { method: "POST" });
  },

  me(): Promise<MeResponse> {
    return request<MeResponse>("/api/v1/auth/me");
  },
};

export type { UserResponse };
