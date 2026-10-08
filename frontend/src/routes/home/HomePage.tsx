import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function HomePage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Home</h1>
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Welcome</CardTitle>
          <CardDescription>
            Ask questions about your documents and spreadsheets, with every figure cited.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Spaces, uploads and chat appear in the navigation as they become available.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
