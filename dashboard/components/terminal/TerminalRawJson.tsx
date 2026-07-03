"use client";

import { useState } from "react";
import { Button } from "@astryxdesign/core/Button";

export function TerminalRawJson({ data, label = "Raw JSON" }: { data: unknown; label?: string }) {
  const [open, setOpen] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
  }

  return (
    <div className="terminalRaw">
      <div className="terminalRawHeader">
        <button className="terminalDisclosure" onClick={() => setOpen((value) => !value)}>
          {open ? "[-]" : "[+]"} {label}
        </button>
        {open ? <Button label="Copy" variant="ghost" size="sm" onClick={copy} /> : null}
      </div>
      {open ? <pre>{JSON.stringify(data, null, 2)}</pre> : null}
    </div>
  );
}
