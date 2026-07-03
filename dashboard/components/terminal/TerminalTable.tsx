import type { ReactNode } from "react";
import { Table } from "@astryxdesign/core/Table";
import { pixel, proportional, type TableColumn } from "@astryxdesign/core/Table/utils";

export type TerminalColumn<T extends object> = {
  key: string;
  header: ReactNode;
  width?: number | "flex";
  align?: "start" | "center" | "end";
  render?: (row: T) => ReactNode;
};

export function TerminalTable<T extends object>({
  data,
  columns,
  idKey,
  emptyLabel = "No data"
}: {
  data: T[];
  columns: TerminalColumn<T>[];
  idKey?: keyof T & string;
  emptyLabel?: string;
}) {
  if (!data.length) {
    return <div className="terminalEmpty">{emptyLabel}</div>;
  }

  const tableColumns: TableColumn<Record<string, unknown>>[] = columns.map((column) => {
    const render = column.render;
    return {
      key: column.key,
      header: column.header,
      align: column.align,
      width: typeof column.width === "number" ? pixel(column.width) : proportional(1),
      renderCell: render ? (row) => render(row as T) : undefined
    };
  });

  return (
    <div className="terminalTableWrap">
      <Table<Record<string, unknown>>
        data={data as Array<Record<string, unknown>>}
        columns={tableColumns}
        idKey={idKey}
        density="compact"
        dividers="grid"
        hasHover
        textOverflow="truncate"
      />
    </div>
  );
}
