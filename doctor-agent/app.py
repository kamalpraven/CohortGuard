"""Clinic A Doctor Agent desktop UI. Exact results stay in this local process."""

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [
    str(ROOT / "doctor-agent"),
    str(ROOT / "coordinator"),
    str(ROOT / "clinic-agents"),
    str(ROOT / "clinic-agents" / "clinic-a-agent"),
]

from doctor_agent.core import LocalContext, local_patient_answer, prepare_coordinator_request
from doctor_agent.submit import FlowerCoordinatorSubmitter

PATIENTS = ROOT / "clinic-agents" / "clinic-a-agent" / "clinic_a" / "data" / "clinic_a_patients.json"
CANARIES = ROOT / "clinic-agents" / "data" / "canaries.json"
COORDINATOR = ROOT / "coordinator"


class DoctorAgentApp:
    def __init__(self, window: tk.Tk) -> None:
        self.window = window
        self.local = LocalContext.load(PATIENTS, CANARIES)
        self.submitter = FlowerCoordinatorSubmitter(COORDINATOR)
        window.title("CohortGuard Doctor Agent — Clinic A local")
        window.geometry("900x650")

        ttk.Label(window, text="Ask a clinical research question").pack(anchor="w", padx=12, pady=(12, 4))
        self.question = tk.Text(window, height=7, wrap="word")
        self.question.pack(fill="x", padx=12)
        self.ask_button = ttk.Button(window, text="Ask", command=self.ask)
        self.ask_button.pack(anchor="w", padx=12, pady=8)
        ttk.Label(window, text="Result (local screen only)").pack(anchor="w", padx=12, pady=(8, 4))
        self.result = tk.Text(window, wrap="word", state="disabled")
        self.result.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _show(self, text: str) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")

    def ask(self) -> None:
        question = self.question.get("1.0", "end").strip()
        if not question:
            self._show("Enter a question.")
            return
        self.ask_button.configure(state="disabled")
        threading.Thread(target=self._answer, args=(question,), daemon=True).start()

    def _answer(self, question: str) -> None:
        try:
            local = local_patient_answer(question, self.local)
            if local is not None:
                # Never print or emit this exact patient-level result.
                text = json.dumps(local, indent=2, ensure_ascii=False)
            else:
                request = prepare_coordinator_request(question, self.local)
                text = self.submitter.submit(request)
        except Exception:
            text = "The request could not be completed. No patient data was sent."
        self.window.after(0, self._finish, text)

    def _finish(self, text: str) -> None:
        self._show(text)
        self.ask_button.configure(state="normal")


def main() -> None:
    window = tk.Tk()
    DoctorAgentApp(window)
    window.mainloop()


if __name__ == "__main__":
    main()
