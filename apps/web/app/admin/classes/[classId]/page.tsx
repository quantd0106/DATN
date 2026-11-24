import { redirect } from 'next/navigation';

export default async function AdminClassIndexPage({
  params,
}: {
  params: Promise<{ classId: string }>;
}) {
  const { classId } = await params;
  redirect(`/admin/classes/${encodeURIComponent(classId)}/settings`);
}
