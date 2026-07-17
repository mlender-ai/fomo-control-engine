import { renderPwaIcon } from "@/lib/pwaIcon";

export const dynamic = "force-static";

export function GET() {
  return renderPwaIcon(192);
}
