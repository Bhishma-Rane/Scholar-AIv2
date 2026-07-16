"""
admin_gui.py
==============
ScholarAI Admin Panel. See admin_gui_core.py for the ProcessManager and
BridgeAPI classes this UI is built on top of -- keep both files in the
same folder, alongside storage_bridge.py.

RUN: python admin_gui.py
"""
import json
import threading
import tkinter.messagebox as messagebox
import tkinter.simpledialog as simpledialog
from pathlib import Path
from datetime import datetime

import customtkinter as ctk

from admin_gui_core import ProcessManager, BridgeAPI, get_bridge_secret, BRIDGE_DIR, BRIDGE_LOCAL_URL, LOG_DIR, PID_DIR, db_connect

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SETTINGS_FILE = BRIDGE_DIR / ".admin_gui_settings.json"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"ngrok_domain": ""}


def save_settings(settings: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


class AdminApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ScholarAI Admin Panel")
        self.geometry("950x650")

        self.settings = load_settings()
        self.pm = ProcessManager(BRIDGE_DIR, LOG_DIR, PID_DIR, ngrok_domain=self.settings.get("ngrok_domain", ""))
        self.api = BridgeAPI(BRIDGE_LOCAL_URL, get_bridge_secret())

        self.tabs = ctk.CTkTabview(self, width=920, height=620)
        self.tabs.pack(padx=15, pady=15, fill="both", expand=True)

        self.tab_dashboard = self.tabs.add("Dashboard")
        self.tab_students = self.tabs.add("Students")
        self.tab_feedback = self.tabs.add("Feedback")
        self.tab_papers = self.tabs.add("Question Papers")

        self._build_dashboard_tab()
        self._build_students_tab()
        self._build_feedback_tab()
        self._build_papers_tab()

        self._refresh_status()

    # -------------------------------------------------------------
    # Dashboard tab: process control
    # -------------------------------------------------------------
    def _build_dashboard_tab(self):
        tab = self.tab_dashboard

        if not get_bridge_secret():
            warning = ctk.CTkLabel(
                tab,
                text="⚠ BRIDGE_SHARED_SECRET is not set in this environment.\n"
                     "Launch this GUI from a terminal where you've already run:\n"
                     "export BRIDGE_SHARED_SECRET=\"...\"",
                text_color="orange", justify="left",
            )
            warning.pack(pady=10)

        status_frame = ctk.CTkFrame(tab)
        status_frame.pack(pady=10, padx=10, fill="x")

        self.status_labels = {}
        for service in ProcessManager.SERVICES:
            row = ctk.CTkFrame(status_frame)
            row.pack(fill="x", pady=4, padx=10)

            label = ctk.CTkLabel(row, text=service.capitalize(), width=100, anchor="w")
            label.pack(side="left", padx=10)

            status_label = ctk.CTkLabel(row, text="checking...", width=100)
            status_label.pack(side="left", padx=10)
            self.status_labels[service] = status_label

            start_btn = ctk.CTkButton(
                row, text="Start", width=80,
                command=lambda s=service: self._run_async(lambda: self._do_start(s)),
            )
            start_btn.pack(side="left", padx=5)

            stop_btn = ctk.CTkButton(
                row, text="Stop", width=80, fg_color="#8B3A3A", hover_color="#6B2A2A",
                command=lambda s=service: self._run_async(lambda: self._do_stop(s)),
            )
            stop_btn.pack(side="left", padx=5)

        button_row = ctk.CTkFrame(tab)
        button_row.pack(pady=15)

        ctk.CTkButton(
            button_row, text="▶ Start All", fg_color="#2E7D32", hover_color="#1B5E20",
            command=lambda: self._run_async(self._do_start_all),
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            button_row, text="■ Stop All", fg_color="#8B3A3A", hover_color="#6B2A2A",
            command=lambda: self._run_async(self._do_stop_all),
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            button_row, text="⟳ Refresh Status",
            command=self._refresh_status,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            button_row, text="⚙ Set ngrok domain",
            command=self._set_ngrok_domain,
        ).pack(side="left", padx=10)

        self.log_box = ctk.CTkTextbox(tab, height=200)
        self.log_box.pack(pady=10, padx=10, fill="both", expand=True)
        self._log("Admin panel ready.")

    def _log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")

    def _run_async(self, fn):
        """Runs a (possibly slow) action on a background thread so the
        GUI doesn't freeze while a subprocess starts or a health check
        is in flight."""
        threading.Thread(target=fn, daemon=True).start()

    def _do_start(self, service: str):
        result = self.pm.start(service)
        self.after(0, lambda: self._log(result))
        self.after(0, self._refresh_status)

    def _do_stop(self, service: str):
        result = self.pm.stop(service)
        self.after(0, lambda: self._log(result))
        self.after(0, self._refresh_status)

    def _do_start_all(self):
        for service in ProcessManager.SERVICES:
            result = self.pm.start(service)
            self.after(0, lambda r=result: self._log(r))
        self.after(0, self._refresh_status)

    def _do_stop_all(self):
        for service in ProcessManager.SERVICES:
            result = self.pm.stop(service)
            self.after(0, lambda r=result: self._log(r))
        self.after(0, self._refresh_status)

    def _refresh_status(self):
        status = self.pm.status_all()
        for service, running in status.items():
            label = self.status_labels[service]
            if running:
                label.configure(text="● RUNNING", text_color="#4CAF50")
            else:
                label.configure(text="○ stopped", text_color="#888888")

    def _set_ngrok_domain(self):
        current = self.settings.get("ngrok_domain", "")
        domain = simpledialog.askstring(
            "ngrok domain", "Enter your ngrok static domain (no https://):", initialvalue=current,
        )
        if domain is not None:
            self.settings["ngrok_domain"] = domain.strip()
            save_settings(self.settings)
            self.pm.ngrok_domain = domain.strip()
            self._log(f"ngrok domain set to: {domain.strip()}")

    # -------------------------------------------------------------
    # Students tab: tier/subscription/password management
    # -------------------------------------------------------------
    def _build_students_tab(self):
        tab = self.tab_students

        top_row = ctk.CTkFrame(tab)
        top_row.pack(fill="x", pady=10, padx=10)
        ctk.CTkButton(top_row, text="⟳ Refresh", command=self._refresh_students).pack(side="left", padx=5)
        ctk.CTkLabel(top_row, text="Tip: change tier/expiry from each student's row below.",
                     text_color="#888888").pack(side="left", padx=15)

        # Header row (fixed, not part of the scrollable area)
        header = ctk.CTkFrame(tab, fg_color="#2b2b2b")
        header.pack(fill="x", padx=10, pady=(5, 0))
        for text, width in [("Username", 160), ("Tier", 90), ("Status", 90), ("Expires", 150), ("Actions", 380)]:
            ctk.CTkLabel(header, text=text, width=width, anchor="w", font=ctk.CTkFont(weight="bold")).pack(
                side="left", padx=8, pady=6
            )

        self.students_scroll = ctk.CTkScrollableFrame(tab, height=420)
        self.students_scroll.pack(pady=(0, 10), padx=10, fill="both", expand=True)

        self._refresh_students()

    def _student_row(self, parent, username: str, tier: str, status: str, expires: str):
        """One row per student: info + inline action buttons. Replaces
        the old single-textbox-plus-one-username-field design, which
        required typing a username by hand for every action -- this way
        every action is right next to the student it affects."""
        row = ctk.CTkFrame(parent)
        row.pack(fill="x", pady=2)

        ctk.CTkLabel(row, text=username, width=160, anchor="w").pack(side="left", padx=8, pady=4)

        tier_color = {"free": "#888888", "gold": "#D4AF37", "diamond": "#5DADE2"}.get(tier, "#888888")
        ctk.CTkLabel(row, text=tier, width=90, anchor="w", text_color=tier_color).pack(side="left", padx=8)

        status_color = "#4CAF50" if status == "active" else "#888888"
        ctk.CTkLabel(row, text=status, width=90, anchor="w", text_color=status_color).pack(side="left", padx=8)

        ctk.CTkLabel(row, text=expires, width=150, anchor="w").pack(side="left", padx=8)

        actions = ctk.CTkFrame(row, fg_color="transparent", width=380)
        actions.pack(side="left", padx=8)

        if status == "active":
            ctk.CTkButton(actions, text="Deactivate", width=90, fg_color="#8B3A3A", hover_color="#6B2A2A",
                          command=lambda u=username: self._deactivate_student(u)).pack(side="left", padx=3)
        else:
            ctk.CTkButton(actions, text="Activate 30d", width=90, fg_color="#2E7D32", hover_color="#1B5E20",
                          command=lambda u=username: self._activate_student(u)).pack(side="left", padx=3)

        tier_var = ctk.StringVar(value=tier)
        tier_menu = ctk.CTkOptionMenu(actions, values=["free", "gold", "diamond"], variable=tier_var, width=85)
        tier_menu.pack(side="left", padx=3)
        ctk.CTkButton(actions, text="Set", width=45,
                      command=lambda u=username, v=tier_var: self._set_student_tier(u, v.get())).pack(side="left", padx=3)

        ctk.CTkButton(actions, text="Reset Pwd", width=80,
                      command=lambda u=username: self._issue_reset_code(u)).pack(side="left", padx=3)

        ctk.CTkButton(actions, text="Delete", width=60, fg_color="#5C1A1A", hover_color="#3D1010",
                      command=lambda u=username: self._delete_student(u)).pack(side="left", padx=3)

    def _refresh_students(self):
        """
        Reads directly from the bridge's database file rather than over
        HTTP, since storage_bridge.py doesn't currently expose a
        'list all users' endpoint (only per-user lookups) -- this is a
        deliberate scope choice rather than adding a new always-open
        admin route. The GUI runs on the SAME machine as the database
        file, so direct SQLite access here is safe and simple.
        """
        import sqlite3

        for widget in self.students_scroll.winfo_children():
            widget.destroy()

        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        if not db_path.exists():
            ctk.CTkLabel(self.students_scroll, text=f"Database not found at {db_path}").pack(pady=20)
            return

        try:
            conn = db_connect(db_path)
            rows = conn.execute(
                "SELECT username, tier, subscription_status, subscription_expires_at "
                "FROM users ORDER BY username"
            ).fetchall()
            conn.close()
        except sqlite3.Error as e:
            ctk.CTkLabel(self.students_scroll, text=f"Database error: {e}").pack(pady=20)
            return

        if not rows:
            ctk.CTkLabel(self.students_scroll, text="No students found.").pack(pady=20)
            return

        for r in rows:
            expires = (r["subscription_expires_at"] or "-")[:10]
            self._student_row(
                self.students_scroll, r["username"], r["tier"] or "free",
                r["subscription_status"] or "inactive", expires,
            )

    def _activate_student(self, username: str):
        import sqlite3
        from datetime import timedelta
        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        expires = (datetime.now() + timedelta(days=30)).isoformat()
        try:
            conn = db_connect(db_path)
            conn.execute(
                "UPDATE users SET subscription_status='active', subscription_expires_at=?, subscription_plan='monthly' WHERE username=?",
                (expires, username),
            )
            conn.commit()
            conn.close()
            self._log(f"Activated '{username}' for 30 days.")
            self._refresh_students()
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))

    def _deactivate_student(self, username: str):
        import sqlite3
        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        try:
            conn = db_connect(db_path)
            conn.execute("UPDATE users SET subscription_status='inactive' WHERE username=?", (username,))
            conn.commit()
            conn.close()
            self._log(f"Deactivated '{username}'.")
            self._refresh_students()
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))

    def _set_student_tier(self, username: str, tier: str):
        import sqlite3
        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        try:
            conn = db_connect(db_path)
            conn.execute("UPDATE users SET tier=? WHERE username=?", (tier, username))
            conn.commit()
            conn.close()
            self._log(f"Set '{username}' to tier '{tier}'.")
            self._refresh_students()
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))

    def _issue_reset_code(self, username: str):
        import sqlite3
        import secrets as secrets_module
        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        try:
            conn = db_connect(db_path)
            conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE username = ? AND used = 0", (username,))
            token = secrets_module.token_urlsafe(6)
            now = datetime.now().timestamp()
            conn.execute(
                "INSERT INTO password_reset_tokens (token, username, created_at, expires_at, used) VALUES (?, ?, ?, ?, 0)",
                (token, username, now, now + 15 * 60),
            )
            conn.commit()
            conn.close()
            self._log(f"Issued reset code for '{username}': {token} (valid 15 min)")
            messagebox.showinfo("Reset code issued", f"Code for {username}:\n\n{token}\n\nValid for 15 minutes.")
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))

    def _delete_student(self, username: str):
        """
        Deletes a student's account entirely: row in `users`, plus
        everything that references it (login tokens, password reset
        tokens, subjects, files-on-disk, feedback, quiz/paper attempts).
        Destructive and irreversible -- confirmed twice given that.
        """
        confirm = messagebox.askyesno(
            "Delete account?",
            f"Permanently delete '{username}' and ALL their data "
            f"(subjects, files, quiz history, feedback)?\n\nThis cannot be undone.",
        )
        if not confirm:
            return

        confirm_again = messagebox.askyesno(
            "Are you sure?",
            f"Really delete '{username}'? Last chance to cancel.",
        )
        if not confirm_again:
            return

        import sqlite3
        import shutil
        db_path = BRIDGE_DIR / "bridge_storage" / "scholarai_bridge.db"
        files_dir = BRIDGE_DIR / "bridge_storage" / "files" / username

        try:
            conn = db_connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            # login_tokens has ON DELETE CASCADE tied to users -- deleting
            # the user row cleans those up automatically. The others
            # (subjects, files, feedback, quiz_attempts, paper_attempts,
            # password_reset_tokens) don't have FK constraints back to
            # users in the current schema, so they're cleaned up explicitly.
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.execute("DELETE FROM subjects WHERE username = ?", (username,))
            conn.execute("DELETE FROM files WHERE username = ?", (username,))
            conn.execute("DELETE FROM feedback WHERE username = ?", (username,))
            conn.execute("DELETE FROM password_reset_tokens WHERE username = ?", (username,))
            conn.execute("DELETE FROM quiz_attempts WHERE username = ?", (username,))
            conn.execute("DELETE FROM paper_attempts WHERE username = ?", (username,))
            conn.execute("DELETE FROM daily_usage WHERE username = ?", (username,))
            conn.commit()
            conn.close()

            if files_dir.exists():
                shutil.rmtree(files_dir, ignore_errors=True)

            self._log(f"Deleted account '{username}' and all associated data.")
            self._refresh_students()
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))

    # -------------------------------------------------------------
    # Feedback tab
    # -------------------------------------------------------------
    def _build_feedback_tab(self):
        tab = self.tab_feedback

        top_row = ctk.CTkFrame(tab)
        top_row.pack(fill="x", pady=10, padx=10)
        ctk.CTkButton(top_row, text="⟳ Refresh", command=self._refresh_feedback).pack(side="left", padx=5)

        self.feedback_box = ctk.CTkTextbox(tab, height=450)
        self.feedback_box.pack(pady=10, padx=10, fill="both", expand=True)

        resolve_row = ctk.CTkFrame(tab)
        resolve_row.pack(fill="x", pady=10, padx=10)
        ctk.CTkLabel(resolve_row, text="Feedback ID:").pack(side="left", padx=5)
        self.feedback_id_entry = ctk.CTkEntry(resolve_row, width=100)
        self.feedback_id_entry.pack(side="left", padx=5)
        ctk.CTkButton(resolve_row, text="Mark Resolved", command=self._resolve_feedback).pack(side="left", padx=5)

        self._refresh_feedback()

    def _refresh_feedback(self):
        self.feedback_box.delete("1.0", "end")
        try:
            items = self.api.list_feedback()
        except Exception as e:
            self.feedback_box.insert("end", f"Could not reach bridge: {e}")
            return

        if not items:
            self.feedback_box.insert("end", "No feedback yet.")
            return

        for item in items:
            when = datetime.fromtimestamp(item["created_at"]).strftime("%Y-%m-%d %H:%M")
            status = "✓ resolved" if item["resolved"] else "● open"
            self.feedback_box.insert("end", f"[#{item['id']}] {item['kind'].upper()} — {item['username']} — {when} — {status}\n")
            if item["kind"] == "rating":
                self.feedback_box.insert("end", f"    Rating: {item['rating']}/5\n\n")
            else:
                self.feedback_box.insert("end", f"    {item['message']}\n\n")

    def _resolve_feedback(self):
        fid_text = self.feedback_id_entry.get().strip()
        if not fid_text.isdigit():
            messagebox.showwarning("Invalid ID", "Enter a numeric feedback ID.")
            return
        try:
            self.api.resolve_feedback(int(fid_text))
            self._log(f"Marked feedback #{fid_text} as resolved.")
            self._refresh_feedback()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # -------------------------------------------------------------
    # Question Papers tab
    # -------------------------------------------------------------
    def _build_papers_tab(self):
        tab = self.tab_papers

        top_row = ctk.CTkFrame(tab)
        top_row.pack(fill="x", pady=10, padx=10)
        ctk.CTkButton(top_row, text="⟳ Refresh", command=self._refresh_papers).pack(side="left", padx=5)

        self.papers_box = ctk.CTkTextbox(tab, height=500)
        self.papers_box.pack(pady=10, padx=10, fill="both", expand=True)

        self._refresh_papers()

    def _refresh_papers(self):
        self.papers_box.delete("1.0", "end")
        try:
            papers = self.api.list_papers(published_only=False)
        except Exception as e:
            self.papers_box.insert("end", f"Could not reach bridge: {e}")
            return

        if not papers:
            self.papers_box.insert("end", "No question papers yet.")
            return

        for p in papers:
            status = "Published" if p.get("published") else "Draft"
            self.papers_box.insert(
                "end",
                f"[#{p['id']}] {p['title']} ({p.get('subject') or 'General'}) — "
                f"{p['total_marks']} marks — {status}\n",
            )


if __name__ == "__main__":
    app = AdminApp()
    app.mainloop()
