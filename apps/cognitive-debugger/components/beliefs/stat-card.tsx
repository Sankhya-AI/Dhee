import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function StatCard({
  label,
  value,
  helper,
}: {
  label: string;
  value: number | string;
  helper: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm text-muted">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold text-ink">{value}</div>
        <p className="mt-2 text-sm text-muted">{helper}</p>
      </CardContent>
    </Card>
  );
}
