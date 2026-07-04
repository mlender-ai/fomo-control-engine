import { ArchivedNotice } from "@/components/archived-notice";

export default function ArchivedJournalPage() {
  return <ArchivedNotice title="저널" replacement={{ href: "/trades", label: "거래 복기로 이동" }} />;
}
