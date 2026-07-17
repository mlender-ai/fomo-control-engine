import { renderPwaIcon } from "@/lib/pwaIcon";

export const size = { height: 64, width: 64 };
export const contentType = "image/png";

export default function Icon() {
  return renderPwaIcon(size.width);
}
