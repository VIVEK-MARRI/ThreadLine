import type { ReactNode } from "react";

/* Table: calm data grid with sticky header option and row actions.
 * On narrow screens wrap in .tl-table-scroll for horizontal scroll. */

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  width?: string;
}

export function Table<T>({
  columns,
  rows,
  keyOf,
  caption,
  empty,
}: {
  columns: TableColumn<T>[];
  rows: T[];
  keyOf: (row: T, index: number) => string;
  caption?: string;
  empty?: ReactNode;
}): React.JSX.Element {
  if (rows.length === 0 && empty) return <>{empty}</>;
  return (
    <div className="tl-table-scroll">
      <table className="tl-table">
        {caption ? <caption className="tl-sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                style={column.width ? { width: column.width } : undefined}
                className={column.align ? `tl-align-${column.align}` : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={keyOf(row, index)}>
              {columns.map((column) => (
                <td key={column.key} className={column.align ? `tl-align-${column.align}` : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
