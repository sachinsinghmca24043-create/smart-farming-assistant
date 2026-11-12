import { NextRequest } from "next/server";

export const FARMER_COOKIE = "farmer_access_token";

export function getFarmerToken(request: NextRequest): string | null {
  return request.cookies.get(FARMER_COOKIE)?.value ?? null;
}

export function getBackendApiUrl(): string {
  const url = process.env.BACKEND_API_BASE_URL;

  if (!url) {
    throw new Error("Backend API URL is not configured.");
  }

  return url.replace(/\/+$/, "");
}