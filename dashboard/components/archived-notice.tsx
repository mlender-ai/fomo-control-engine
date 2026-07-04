import Link from "next/link";
import { Archive } from "lucide-react";

export function ArchivedNotice({ title, replacement }: { title: string; replacement?: { href: string; label: string } }) {
  return (
    <div className="page">
      <div className="archivedNotice">
        <Archive size={28} />
        <h1>{title} 화면은 보관됐습니다</h1>
        <p>Phase 4 정보 구조 개편으로 이 화면은 더 이상 유지되지 않습니다. 코드는 삭제되지 않고 보관 상태입니다.</p>
        <div className="archivedNoticeActions">
          <Link className="button" href="/">
            라이브 포지션 관제로 이동
          </Link>
          {replacement ? (
            <Link className="button secondary" href={replacement.href}>
              {replacement.label}
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}
