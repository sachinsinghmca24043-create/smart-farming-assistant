import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { timingSafeEqual } from "node:crypto";
import AdminDashboard from "./AdminDashboard";

export const runtime = "nodejs";

export default async function AdminPage() {
  const cookieStore = await cookies();

  const supplied = Buffer.from(
    cookieStore.get("admin_session")?.value ?? ""
  );

  const expected = Buffer.from(
    process.env.ADMIN_SESSION_TOKEN ?? ""
  );

  const authenticated =
    expected.length > 0 &&
    supplied.length === expected.length &&
    timingSafeEqual(supplied, expected);

  if (!authenticated) {
    redirect("/admin/login");
  }

  return <AdminDashboard />;
}