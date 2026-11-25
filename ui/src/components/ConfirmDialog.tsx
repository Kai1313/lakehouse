import { useEffect, useId, useRef } from "react";
import { AlertTriangle } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Delete",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  function handleClose() {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const action = dialog.returnValue;
    dialog.returnValue = "";
    if (action === "confirm") {
      onConfirm();
    } else {
      onCancel();
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={handleClose}
      className="m-auto w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 text-slate-100 shadow-2xl"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-rose-500/15 text-rose-400">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h1 id={titleId} className="text-base font-semibold text-slate-100">
            {title}
          </h1>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-300">{description}</p>
        </div>
      </div>

      <form method="dialog" className="mt-6 flex justify-end gap-2.5">
        <button
          type="submit"
          value="cancel"
          autoFocus
          className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          Cancel
        </button>
        <button
          type="submit"
          value="confirm"
          className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 focus-visible:outline-2 focus-visible:outline-rose-400"
        >
          {confirmLabel}
        </button>
      </form>
    </dialog>
  );
}
