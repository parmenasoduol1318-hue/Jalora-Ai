from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

import httpx
import pywintypes

from .executor import WindowsActionError, WindowsExecutor
from .protocol import (
    DeviceProtocolClient,
    ProtocolHTTPError,
    parse_pairing_link,
)
from .security import (
    clear_credentials,
    load_config,
    load_credentials,
    save_config,
    save_credentials,
)

_SCOPE_LABELS = {
    "windows.apps.launch": "Launch/close allowlisted apps",
    "windows.screen.read": "Read accessibility controls/OCR (private screen text is sent to Jalora)",
    "windows.screen.control": "Click, scroll, and enter approved text in accessible controls",
}


class PendingCommand:
    def __init__(self, command: dict) -> None:
        self.command = command
        self.approved = False
        self.completed = threading.Event()
        self.dialog: tk.Toplevel | None = None


class JaloraWindowsCompanion:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Jalora Windows Companion")
        self.root.geometry("700x690")
        self.root.minsize(560, 560)
        self.config = load_config()
        self.credentials = load_credentials()
        self.executor = WindowsExecutor()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.paused = False
        self.manifest_changed = threading.Event()
        self.worker: threading.Thread | None = None
        self.pending_command: PendingCommand | None = None
        self.pending_disconnect = False
        self.status = tk.StringVar(value="Paired" if self.credentials else "Not paired")
        self.pairing_link = tk.StringVar()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(150, self._drain_events)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        ttk.Label(
            container,
            text="Jalora Windows Companion",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "Outbound-only connection. The companion executes only apps you add "
                "here and asks before every received action."
            ),
            wraplength=560,
        ).pack(anchor="w", pady=(4, 12))

        pairing = ttk.LabelFrame(container, text="Pair with Jalora", padding=10)
        pairing.pack(fill="x")
        ttk.Label(pairing, text="Paste the pairing link created in Jalora Integrations:").pack(
            anchor="w"
        )
        ttk.Entry(pairing, textvariable=self.pairing_link).pack(fill="x", pady=6)
        self.pair_button = ttk.Button(pairing, text="Review and pair", command=self._pair)
        self.pair_button.pack(anchor="e")

        apps_frame = ttk.LabelFrame(container, text="Local application allowlist", padding=10)
        apps_frame.pack(fill="both", expand=True, pady=(12, 0))
        ttk.Label(
            apps_frame,
            text="Only exact .exe files selected by you may be launched.",
        ).pack(anchor="w")
        self.apps_list = tk.Listbox(apps_frame, height=6, exportselection=False)
        self.apps_list.pack(fill="both", expand=True, pady=6)
        buttons = ttk.Frame(apps_frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Add .exe", command=self._add_application).pack(side="left")
        ttk.Button(
            buttons,
            text="Discover installed apps",
            command=self._discover_applications,
        ).pack(side="left", padx=6)
        ttk.Button(buttons, text="Remove selected", command=self._remove_application).pack(
            side="left", padx=6
        )

        history_frame = ttk.LabelFrame(container, text="Recent actions (this session)", padding=8)
        history_frame.pack(fill="x", pady=(10, 0))
        self.history = ttk.Treeview(
            history_frame,
            columns=("time", "operation", "result"),
            show="headings",
            height=5,
        )
        self.history.heading("time", text="Time")
        self.history.heading("operation", text="Operation")
        self.history.heading("result", text="Result")
        self.history.column("time", width=90, stretch=False)
        self.history.column("operation", width=260, stretch=True)
        self.history.column("result", width=150, stretch=False)
        self.history.pack(fill="x")

        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=400).pack(side="left", anchor="w")
        self.start_button = ttk.Button(footer, text="Connect", command=self._start)
        self.start_button.pack(side="right")
        self.pause_button = ttk.Button(
            footer,
            text="Pause",
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_button.pack(side="right", padx=(0, 6))
        self.stop_button = ttk.Button(
            footer,
            text="Stop",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="right", padx=(0, 6))
        ttk.Button(footer, text="Disconnect", command=self._disconnect).pack(
            side="right", padx=(0, 6)
        )
        self._render_apps()

    def _render_apps(self) -> None:
        self.apps_list.delete(0, tk.END)
        for app in self.config["applications"]:
            self.apps_list.insert(tk.END, f"{app['name']}  ·  {app['path']}")

    def _add_application(self) -> None:
        path_text = filedialog.askopenfilename(
            title="Choose one Windows application",
            filetypes=[("Windows applications", "*.exe")],
        )
        if not path_text:
            return
        path = Path(path_text).resolve()
        if path.suffix.casefold() != ".exe" or not path.is_file():
            messagebox.showerror("Invalid application", "Choose an existing .exe file.")
            return
        name = simpledialog.askstring(
            "Application name",
            "Choose a name Jalora can use for this application:",
            initialvalue=path.stem,
            parent=self.root,
        )
        if not name or not name.strip():
            return
        self._store_application(path, name)

    def _store_application(self, path: Path, name: str) -> None:
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            messagebox.showerror(
                "Invalid application",
                f"The selected executable is not available: {exc}",
                parent=self.root,
            )
            return
        if path.suffix.casefold() != ".exe" or not path.is_file():
            messagebox.showerror(
                "Invalid application",
                "Choose an existing .exe file.",
                parent=self.root,
            )
            return
        digest_state = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest_state.update(block)
        except OSError as exc:
            messagebox.showerror(
                "Application unavailable",
                f"Could not verify the selected executable: {exc}",
                parent=self.root,
            )
            return
        digest = digest_state.hexdigest()
        app_id = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:20]
        applications = [
            item for item in self.config["applications"] if item.get("id") != app_id
        ]
        applications.append({
            "id": app_id,
            "name": name.strip()[:120],
            "path": str(path),
            "sha256": digest,
        })
        try:
            save_config({**self.config, "applications": applications})
        except OSError as exc:
            messagebox.showerror(
                "Could not save application",
                f"The local allowlist was not saved: {exc}",
                parent=self.root,
            )
            return
        self.config["applications"] = applications
        self.manifest_changed.set()
        self._render_apps()
        self.status.set("Application added to this computer's local allowlist.")

    def _discover_applications(self) -> None:
        try:
            applications = self.executor.discover_installed_applications()
        except (ImportError, OSError, RuntimeError) as exc:
            messagebox.showerror(
                "Application discovery failed",
                str(exc),
                parent=self.root,
            )
            return
        if not applications:
            messagebox.showinfo(
                "No discoverable applications",
                "No classic desktop applications with a registered .exe were found.",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Discover installed desktop apps")
        dialog.transient(self.root)
        dialog.geometry("620x460")
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "Choose apps to add to this computer's local allowlist. "
                "Discovery does not grant Jalora access; selected executables "
                "are shared only after you connect."
            ),
            wraplength=560,
        ).pack(anchor="w", pady=(0, 8))
        entries = tk.Listbox(body, selectmode=tk.EXTENDED, exportselection=False)
        entries.pack(fill="both", expand=True)
        for application in applications:
            entries.insert(
                tk.END,
                f"{application['name']}  ·  {application['path']}",
            )
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))

        def add_selected() -> None:
            selected = entries.curselection()
            if not selected:
                messagebox.showinfo(
                    "Select applications",
                    "Choose at least one application to add.",
                    parent=dialog,
                )
                return
            for index in selected:
                application = applications[index]
                self._store_application(
                    Path(application["path"]),
                    application["name"],
                )
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Add selected", command=add_selected).pack(
            side="right",
            padx=(0, 8),
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def _remove_application(self) -> None:
        selected = self.apps_list.curselection()
        if not selected:
            return
        app = self.config["applications"][selected[0]]
        if not messagebox.askyesno(
            "Remove application",
            f"Remove {app['name']} from the companion allowlist?",
            parent=self.root,
        ):
            return
        self.config["applications"].remove(app)
        save_config(self.config)
        self.manifest_changed.set()
        self._render_apps()
        self.status.set("Application removed. Reconnect to publish the updated allowlist.")

    def _pair(self) -> None:
        try:
            link = parse_pairing_link(self.pairing_link.get())
            client = DeviceProtocolClient(link.api_base)
            try:
                request = client.inspect_pairing(link)
                if request.get("platform") != "windows":
                    raise ValueError("This pairing request is not for a Windows device.")
                if request.get("status") != "pending":
                    raise ValueError(f"This pairing request is {request.get('status')}.")
                scopes = request.get("scopes", [])
                if not isinstance(scopes, list) or not scopes:
                    raise ValueError("The pairing request contains no permissions.")
                scope_summary = "\n".join(
                    f"• {_SCOPE_LABELS.get(scope, scope)}"
                    for scope in scopes
                )
                if not messagebox.askyesno(
                    "Review Windows device pairing",
                    (
                        f"Jalora account: {request.get('requested_by', 'unknown')}\n"
                        f"Jalora server: {link.api_base}\n"
                        f"Device name: {request.get('name', 'Windows device')}\n\n"
                        f"Requested permissions:\n{scope_summary}\n\n"
                        "Screen-read permission sends accessible control names and "
                        "visible text to Jalora. Every action still needs a separate "
                        "local confirmation. Approve this pairing?"
                    ),
                    parent=self.root,
                ):
                    return
                credentials = client.approve_pairing(link)
            finally:
                client.close()
            device = credentials.get("device", {})
            self.credentials = {
                "api_base": link.api_base,
                "device_id": device.get("id", ""),
                "device_name": device.get("name", request.get("name", "")),
                "device_token": credentials["device_token"],
                "scopes": list(device.get("scopes", scopes)),
                "pending_receipt": None,
            }
            save_credentials(self.credentials)
            self.pairing_link.set("")
            self.status.set(
                f"Paired with {self.credentials['device_name']}. "
                "Credential encrypted for this Windows account."
            )
        except (KeyError, RuntimeError, TypeError, ValueError, httpx.RequestError) as exc:
            messagebox.showerror("Pairing failed", str(exc), parent=self.root)
            self.status.set(f"Pairing failed: {exc}")

    def _start(self) -> None:
        if not self.credentials:
            messagebox.showinfo("Pair first", "Pair this Windows device before connecting.")
            return
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._connection_loop, daemon=True)
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.pause_button.configure(state="normal")
        self.status.set("Connecting to Jalora over outbound HTTPS…")

    def _stop(self) -> None:
        self.stop_event.set()
        self.paused = False
        self.pause_button.configure(text="Pause")
        pending = self.pending_command
        if pending and not pending.completed.is_set():
            pending.approved = False
            pending.completed.set()
            if pending.dialog is not None and pending.dialog.winfo_exists():
                pending.dialog.destroy()
        self.status.set("Stopping. An already-running Windows action may remain uncertain.")
        self.stop_button.configure(state="disabled")
        self.pause_button.configure(state="disabled")

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.configure(text="Resume" if self.paused else "Pause")
        self.status.set(
            "Paused. No new device command will be approved or executed."
            if self.paused
            else "Resuming the outbound companion connection."
        )

    def _disconnect(self) -> None:
        if not messagebox.askyesno(
            "Disconnect Windows companion",
            "Remove this computer's encrypted pairing credential? Re-pairing will be required.",
            parent=self.root,
        ):
            return
        if self.worker and self.worker.is_alive():
            self.pending_disconnect = True
            self._stop()
            return
        self._finish_disconnect()

    def _finish_disconnect(self) -> None:
        clear_credentials()
        self.credentials = None
        self.pending_disconnect = False
        self.status.set("Disconnected. Revoke the device in Jalora Integrations as well.")
        self.start_button.configure(state="normal")

    def _connection_loop(self) -> None:
        client = DeviceProtocolClient(self.credentials["api_base"])
        access_token = ""
        refresh_at = 0.0
        last_manifest = ""
        try:
            while not self.stop_event.is_set():
                while self.paused and not self.stop_event.wait(0.2):
                    pass
                if self.stop_event.is_set():
                    break
                try:
                    if not access_token or time.monotonic() >= refresh_at:
                        session = client.create_session(self.credentials["device_token"])
                        access_token = session["access_token"]
                        refresh_at = time.monotonic() + 240
                    manifest = self.executor.manifest(
                        self.config["applications"],
                        self.credentials.get("scopes", []),
                    )
                    windows_scopes = {
                        "windows.apps.launch",
                        "windows.screen.read",
                        "windows.screen.control",
                    }.intersection(self.credentials.get("scopes", []))
                    manifest_digest = json.dumps(manifest, sort_keys=True)
                    if windows_scopes and (
                        manifest_digest != last_manifest or self.manifest_changed.is_set()
                    ):
                        client.report_manifest(access_token, manifest)
                        last_manifest = manifest_digest
                        self.manifest_changed.clear()
                    self.events.put(("status", "Connected. Waiting for approved Jalora actions."))
                    self._deliver_pending_receipt(client, access_token)
                    if self.stop_event.is_set():
                        break
                    batch = client.poll(access_token)
                    for command in batch.get("commands", []):
                        while self.paused and not self.stop_event.wait(0.2):
                            pass
                        if self.stop_event.is_set():
                            break
                        self._handle_command(client, access_token, command)
                except ProtocolHTTPError as exc:
                    if exc.status_code == 401:
                        try:
                            session = client.create_session(self.credentials["device_token"])
                            access_token = session["access_token"]
                            refresh_at = time.monotonic() + 240
                            continue
                        except ProtocolHTTPError as session_error:
                            if session_error.status_code == 401:
                                clear_credentials()
                                self.credentials = None
                                self.events.put((
                                    "status",
                                    "Jalora no longer authorizes this device. Pair again if appropriate.",
                                ))
                                return
                            raise
                    self.events.put(("status", f"Jalora protocol error: {exc}"))
                    self.stop_event.wait(3)
                except (
                    httpx.RequestError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    pywintypes.error,
                ) as exc:
                    self.events.put(("status", f"Connection/action error: {exc}. Retrying safely."))
                    self.stop_event.wait(3)
        finally:
            client.close()
            self.events.put(("stopped", None))

    def _deliver_pending_receipt(
        self,
        client: DeviceProtocolClient,
        access_token: str,
    ) -> None:
        pending = self.credentials.get("pending_receipt")
        if not isinstance(pending, dict):
            return
        client.receipt(access_token, pending["command_id"], pending["body"])
        self.credentials["pending_receipt"] = None
        save_credentials(self.credentials)
        self.events.put(("status", "Recovered a saved action receipt; no action was repeated."))

    def _handle_command(
        self,
        client: DeviceProtocolClient,
        access_token: str,
        command: dict,
    ) -> None:
        pending = PendingCommand(command)
        self.pending_command = pending
        self.events.put(("approval", pending))
        while not pending.completed.wait(0.2):
            if self.stop_event.is_set():
                pending.approved = False
                pending.completed.set()
                break
        decision = client.decide(
            access_token,
            command["command_id"],
            command["nonce"],
            pending.approved,
        )
        if not pending.approved or not decision.get("execute"):
            self.events.put((
                "history",
                (command.get("operation", "unknown"), "rejected"),
            ))
            self.events.put(("status", f"{command['operation']} was not executed."))
            self.pending_command = None
            return
        try:
            status, evidence, error_code = self.executor.execute(
                command["operation"],
                command.get("arguments", {}),
                self.config["applications"],
            )
        except (
            WindowsActionError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            pywintypes.error,
        ) as exc:
            status = "uncertain"
            evidence = {"reason": f"Windows action outcome could not be verified: {exc}"[:500]}
            error_code = (
                exc.code
                if isinstance(exc, WindowsActionError)
                else "WINDOWS_ACTION_OUTCOME_UNKNOWN"
            )
        receipt_body = {
            "nonce": command["nonce"],
            "status": status,
            "evidence": evidence,
            "error_code": error_code,
        }
        self.credentials["pending_receipt"] = {
            "command_id": command["command_id"],
            "body": receipt_body,
        }
        save_credentials(self.credentials)
        client.receipt(access_token, command["command_id"], receipt_body)
        self.credentials["pending_receipt"] = None
        save_credentials(self.credentials)
        self.events.put((
            "status",
            f"Action receipt recorded as {status}. Jalora does not retry uncertain actions.",
        ))
        self.events.put(("history", (command.get("operation", "unknown"), status)))
        self.pending_command = None

    def _drain_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "status":
                self.status.set(payload)
            elif event == "approval":
                self._ask_local_approval(payload)
            elif event == "history":
                operation, result = payload
                self.history.insert(
                    "",
                    0,
                    values=(
                        datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S"),
                        operation,
                        result,
                    ),
                )
                rows = self.history.get_children()
                for row in rows[50:]:
                    self.history.delete(row)
            elif event == "stopped":
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.pause_button.configure(state="disabled", text="Pause")
                if self.pending_disconnect:
                    self._finish_disconnect()
                else:
                    self.status.set("Companion stopped.")
        self.root.after(150, self._drain_events)

    def _ask_local_approval(self, pending: PendingCommand) -> None:
        command = pending.command
        operation = command.get("operation", "unknown")
        arguments = command.get("arguments", {})
        app = next(
            (item for item in self.config["applications"] if item["id"] == arguments.get("app_id")),
            None,
        )
        app_name = app["name"] if app else "Windows application"
        dialog = tk.Toplevel(self.root)
        pending.dialog = dialog
        dialog.title("Review Windows action")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=f"{operation} · {app_name}",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Jalora already recorded its task-level approval. Confirm this exact "
                "operation on the Windows device before it runs."
            ),
            wraplength=500,
        ).pack(anchor="w", pady=(6, 8))
        details = tk.Text(body, height=12, wrap="word")
        details.pack(fill="both", expand=True)
        details.insert("1.0", json.dumps(arguments, ensure_ascii=False, indent=2))
        details.configure(state="disabled")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(
            buttons,
            text="Reject",
            command=lambda: self._resolve_pending(pending, False),
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="Approve once",
            command=lambda: self._resolve_pending(pending, True),
        ).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._resolve_pending(pending, False))
        dialog.lift()
        dialog.focus_force()
        self.status.set("A Windows action needs local approval.")

    @staticmethod
    def _resolve_pending(pending: PendingCommand, approved: bool) -> None:
        if pending.completed.is_set():
            return
        pending.approved = approved
        pending.completed.set()
        if pending.dialog is not None and pending.dialog.winfo_exists():
            pending.dialog.destroy()

    def _close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                "Stop Jalora companion",
                "Stop the connection and close the window? An action already in progress "
                "cannot be rolled back and may be reported as uncertain.",
                parent=self.root,
            ):
                return
            self._stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        JaloraWindowsCompanion(root)
    except (OSError, RuntimeError, TypeError) as exc:
        messagebox.showerror("Jalora Windows Companion", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
