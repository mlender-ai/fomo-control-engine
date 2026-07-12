import { redirect } from "next/navigation";

export default function CalibrationPage() {
  redirect("/engine?tab=status");
}
