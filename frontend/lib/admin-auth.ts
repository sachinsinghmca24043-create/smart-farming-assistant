import { NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";

export function isAdminAuthenticated(request: NextRequest): boolean {
  const expectedToken = process.env.ADMIN_SESSION_TOKEN;
  const suppliedToken = request.cookies.get("admin_session")?.value;

  if (!expectedToken || !suppliedToken) return false;

  const expected = Buffer.from(expectedToken);
  const supplied = Buffer.from(suppliedToken);

  return (
    expected.length === supplied.length &&
    timingSafeEqual(expected, supplied)
  );
}