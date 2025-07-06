import sys
import os
import subprocess
import json
import platform
import re # Import re for parsing requirements
from pathlib import Path
import requests # For downloading model files
import shutil   # For saving downloaded files safely
import os       # Needed for os.remove in _download_file cleanup
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel,
    QMessageBox, QApplication, QWidget # Removed QProgressDialog as we use QTextEdit
)
from PyQt6.QtCore import pyqtSignal, QObject, QRunnable, pyqtSlot, QThreadPool, Qt
from PyQt6.QtGui import QFont

# Assuming config and workers are accessible relative to the project root
# Make sure config is imported correctly based on your project structure
try:
    import config
    from utils.workers import Worker, WorkerSignals # Assuming WorkerSignals exists or is part of Worker
except ImportError:
    # Fallback for potential path issues if run standalone or structure changes
    print("Warning: Could not import config or utils.workers directly. Assuming paths relative to script.")
    # You might need to adjust sys.path or use relative imports differently
    # For now, define BASE_DIR manually for robustness if config fails
    BASE_DIR_FALLBACK = Path(__file__).resolve().parent.parent # Assumes dialogs/ is one level down from root
    config = type('obj', (object,), {'BASE_DIR': BASE_DIR_FALLBACK})() # Mock config object
    # Mock Worker class if needed for standalone testing without utils
    class WorkerSignals(QObject):
        finished = pyqtSignal()
        error = pyqtSignal(tuple)
        result = pyqtSignal(object)
        progress = pyqtSignal(str) # Add progress signal
    class Worker(QRunnable):
        def __init__(self, fn, *args, **kwargs):
            super().__init__()
            self.fn = fn
            self.args = args
            self.kwargs = kwargs
            self.signals = WorkerSignals()
            # Add callback for progress
            self.kwargs['progress_callback'] = self.signals.progress.emit

        @pyqtSlot()
        def run(self):
            try:
                result = self.fn(*self.args, **self.kwargs)
            except Exception as e:
                import traceback
                traceback.print_exc()
                exctype, value = sys.exc_info()[:2]
                self.signals.error.progress_callback((exctype, value, traceback.format_exc()))
            else:
                self.signals.result.progress_callback(result)
            finally:
                self.signals.finished.progress_callback()


# Define VENV path relative to project root
VENV_PATH = Path(config.BASE_DIR) / ".venv"
REQ_FILE = Path(config.BASE_DIR) / "requirements.txt"

# --- Model Download URLs ---
MODEL_URL = "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx"
TAGS_URL = "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/raw/main/selected_tags.csv"

# --- Static Check Function ---
def check_critical_requirements() -> bool:
    """
    Performs a quick check for critical runtime components (e.g., onnxruntime).
    Returns True if basic import works, False otherwise.
    """
    try:
        # The most common failure point was onnxruntime import
        import onnxruntime
        # Could add other critical checks here if needed
        print("DEBUG: Critical requirement check (onnxruntime import) successful.") # Optional debug log
        return True
    except ImportError as e:
        print(f"DEBUG: Critical requirement check failed: {e}") # Optional debug log
        return False
    except Exception as e: # Catch other potential errors during import
        print(f"DEBUG: Critical requirement check failed with unexpected error: {e}")
        return False


class RequirementsDialog(QDialog):
    # Signals for worker communication
    updateStatusSignal = pyqtSignal(str)
    checkCompleteSignal = pyqtSignal(dict) # Emits dict with check results
    installCompleteSignal = pyqtSignal(bool, str) # Emits success/fail bool and message

    # Signal to potentially inform main window about overall status
    requirementsMetStatus = pyqtSignal(bool)

    def __init__(self, parent=None, run_checks_on_init=True): # Add parameter to control initial check
        super().__init__(parent)
        self.setWindowTitle("Check Requirements")
        self.setMinimumSize(650, 500) # Slightly larger default size

        # State
        self.check_results: Dict[str, any] = {}
        self.is_checking = False
        self.is_installing = False
        # Use main window's threadpool if passed, otherwise create one
        self.threadpool = getattr(parent, 'threadpool', QThreadPool())
        if not isinstance(self.threadpool, QThreadPool):
             print("Warning: Parent does not have QThreadPool, creating local one.")
             self.threadpool = QThreadPool()


        # --- UI Elements ---
        layout = QVBoxLayout(self)

        self.status_label = QLabel("Click 'Check Requirements' to verify the setup.")
        layout.addWidget(self.status_label)

        self.status_details_area = QTextEdit()
        self.status_details_area.setReadOnly(True)
        self.status_details_area.setFont(QFont("Courier New", 9))
        self.status_details_area.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap) # Keep no wrap
        layout.addWidget(self.status_details_area)

        # --- Status Indicators ---
        status_indicators_widget = QWidget() # Use a widget for background styling if desired
        status_indicators_layout = QHBoxLayout(status_indicators_widget)
        status_indicators_layout.setContentsMargins(5, 2, 5, 2) # Add some padding

        self.python_status_label = QLabel("Python: ?")
        self.venv_status_label = QLabel("Venv: ?")
        self.pip_status_label = QLabel("Pip: ?")
        self.packages_status_label = QLabel("Packages: ?")
        self.onnx_status_label = QLabel("ONNX Runtime: ?")
        self.model_file_status_label = QLabel("Model File: ?") # New label for ONNX model file
        self.tags_file_status_label = QLabel("Tags File: ?")   # New label for CSV tags file

        # Add labels with some spacing/stretch
        status_indicators_layout.addWidget(self.python_status_label)
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.venv_status_label)
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.pip_status_label)
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.packages_status_label)
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.onnx_status_label)
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.model_file_status_label) # Add new label
        status_indicators_layout.addStretch()
        status_indicators_layout.addWidget(self.tags_file_status_label)  # Add new label

        layout.addWidget(status_indicators_widget)
        # --- End Status Indicators ---

        button_layout = QHBoxLayout()
        self.check_button = QPushButton("Check Requirements")
        self.install_button = QPushButton("Install/Fix Requirements")
        self.install_button.setEnabled(False) # Disabled until checks show issues
        self.close_button = QPushButton("Close")

        button_layout.addWidget(self.check_button)
        button_layout.addWidget(self.install_button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)

        # --- Signal Connections ---
        self.check_button.clicked.connect(self.start_checks)
        self.install_button.clicked.connect(self.start_installation)
        self.close_button.clicked.connect(self.accept) # QDialog's accept closes it

        self.updateStatusSignal.connect(self.append_status_message)
        self.checkCompleteSignal.connect(self.handle_check_completion)
        self.installCompleteSignal.connect(self.handle_install_completion)

        # Checks are no longer run automatically on init.
        # User must click the button.
        self.status_label.setText("Click 'Check Requirements' to verify the setup.")
        # Ensure install button is disabled initially
        self.install_button.setEnabled(False)

    # --- UI Update Slots ---

    @pyqtSlot(str)
    def append_status_message(self, message: str):
        """Appends a message to the status text area."""
        self.status_details_area.append(message)
        # Auto-scroll to the bottom
        self.status_details_area.verticalScrollBar().setValue(
            self.status_details_area.verticalScrollBar().maximum()
        )
        QApplication.processEvents() # Keep UI responsive

    def set_ui_busy(self, busy: bool):
        """Disables/Enables buttons during operations."""
        # self.is_checking = busy if busy else self.is_checking # REMOVED: State flags are managed by calling slots
        # self.is_installing = busy if busy else self.is_installing # REMOVED: State flags are managed by calling slots
        is_currently_busy = self.is_checking or self.is_installing # Read current state flags

        self.check_button.setEnabled(not is_currently_busy)
        # Install button enabled only if not busy AND checks indicated need for install
        self.install_button.setEnabled(not is_currently_busy and self.check_results.get("needs_install", False))
        self.close_button.setEnabled(not is_currently_busy)

    @pyqtSlot(dict)
    def handle_check_completion(self, results: dict):
        """Updates the UI based on the results from the check worker."""
        print("DEBUG: handle_check_completion slot entered.") # DEBUG LOG
        self.is_checking = False # Mark checking as finished
        self.append_status_message("\nCheck complete.")
        self.check_results = results

        # --- Update Status Labels ---
        ok_style = "color: green; font-weight: bold;"
        fail_style = "color: red; font-weight: bold;"
        na_style = "color: gray;"

        self.python_status_label.setText(f"Python: {'OK' if results.get('python_ok') else 'FAIL'}")
        self.python_status_label.setStyleSheet(ok_style if results.get('python_ok') else fail_style)

        self.venv_status_label.setText(f"Venv: {'OK' if results.get('venv_ok') else 'FAIL'}")
        self.venv_status_label.setStyleSheet(ok_style if results.get('venv_ok') else fail_style)

        self.pip_status_label.setText(f"Pip: {'OK' if results.get('pip_ok') else 'FAIL'}")
        self.pip_status_label.setStyleSheet(ok_style if results.get('pip_ok') else fail_style if results.get('venv_ok') else na_style) # NA if venv failed

        self.packages_status_label.setText(f"Packages: {'OK' if results.get('packages_ok') else 'FAIL'}")
        self.packages_status_label.setStyleSheet(ok_style if results.get('packages_ok') else fail_style if results.get('pip_ok') else na_style) # NA if pip failed

        onnx_status_text = "ONNX Runtime: "
        onnx_style = na_style
        if results.get('pip_ok'): # Only show ONNX status if pip check was possible
            if results.get('gpu_detected') is True:
                if results.get('onnx_ok') is True:
                    onnx_status_text += "GPU OK"
                    onnx_style = ok_style
                elif results.get('onnx_ok') is False:
                    onnx_status_text += "GPU FAIL (Package Issue)"
                    onnx_style = fail_style
                else:
                    onnx_status_text += "GPU ?"
                    onnx_style = na_style
            else: # No GPU detected
                if results.get('onnx_ok') is True:
                    onnx_status_text += "CPU OK"
                    onnx_style = ok_style
                elif results.get('onnx_ok') is False:
                    onnx_status_text += "CPU FAIL (Package Issue)"
                    onnx_style = fail_style
                else:
                    onnx_status_text += "CPU ?"
                    onnx_style = na_style
        else:
             onnx_status_text += "N/A"

        self.onnx_status_label.setText(onnx_status_text)
        self.onnx_status_label.setStyleSheet(onnx_style)

        # Update Model File Status Label
        model_file_ok = results.get('model_file_ok')
        self.model_file_status_label.setText(f"Model File: {'OK' if model_file_ok else 'FAIL' if model_file_ok is False else '?'}")
        self.model_file_status_label.setStyleSheet(ok_style if model_file_ok else fail_style if model_file_ok is False else na_style)

        # Update Tags File Status Label
        tags_file_ok = results.get('tags_file_ok')
        self.tags_file_status_label.setText(f"Tags File: {'OK' if tags_file_ok else 'FAIL' if tags_file_ok is False else '?'}")
        self.tags_file_status_label.setStyleSheet(ok_style if tags_file_ok else fail_style if tags_file_ok is False else na_style)

        self.onnx_status_label.setText(onnx_status_text)
        self.onnx_status_label.setStyleSheet(onnx_style)
        # --- End Status Labels ---

        overall_status_ok = results.get('overall_ok', False)
        self.requirementsMetStatus.emit(overall_status_ok) # Emit overall status

        if results.get("needs_install", False):
            self.status_label.setText("Issues found. Click 'Install/Fix Requirements'.")
            self.append_status_message("-> Issues found requiring installation/fix.")
            if results.get("missing_packages"):
                 self.append_status_message(f"   Missing/Incorrect: {', '.join(results['missing_packages'])}")
            if not results.get("venv_ok"):
                 self.append_status_message("   Virtual environment needs creation.")
            if not results.get("pip_ok") and results.get("venv_ok"):
                 self.append_status_message("   Pip installation within venv might be needed.")
        elif overall_status_ok:
            self.status_label.setText("All checks passed successfully.")
            self.append_status_message("-> All checks passed.")
        else:
            # This case might happen if checks passed individually but something else failed
            self.status_label.setText("Checks completed, but some issues persist (see details).")
            self.append_status_message("-> Checks completed, but requirements not fully met.")

        self.set_ui_busy(False) # Update button states based on final check results

    @pyqtSlot(bool, str)
    def handle_install_completion(self, success: bool, message: str):
        """Handles the result of the installation worker."""
        print(f"DEBUG: handle_install_completion slot entered. Success: {success}") # DEBUG LOG
        self.is_installing = False # Mark installing as finished
        self.append_status_message(f"\nInstallation Result: {'Success' if success else 'Failed'}")
        self.append_status_message(message)
        # Always re-run checks after any install attempt to reflect the current state
        self.status_label.setText("Installation attempt finished. Re-running checks...")
        if not success:
             # Show warning only if it failed, but still re-run checks
             QMessageBox.warning(self, "Installation Attempt Failed", "The installation process failed or finished with errors. Re-running checks to see the current status. See details in the log.")
        # Automatically re-check after any install attempt
        self.start_checks(is_recheck=True) # Pass True when re-checking
        # Note: set_ui_busy(False) will be called by the subsequent handle_check_completion

    @pyqtSlot(tuple)
    def handle_worker_error(self, error_info: tuple):
        """Handles errors reported by worker threads."""
        # Determine if checking or installing when error occurred
        operation = "Installation" if self.is_installing else "Check"
        self.is_checking = False
        self.is_installing = False

        exception, value, traceback_str = error_info # Expecting 3 values now
        error_message = f"An error occurred during {operation}: {value}\n{traceback_str}"
        self.append_status_message(f"\nERROR:\n{error_message}")
        print(f"Worker Error: {error_message}") # Also print to console
        QMessageBox.critical(self, f"{operation} Error", f"An unexpected error occurred during {operation}:\n{value}")
        self.set_ui_busy(False) # Ensure UI is re-enabled on error

    def reset_status_labels(self):
        """Resets status indicator labels to default state."""
        default_style = "" # Or a specific default style
        self.python_status_label.setText("Python: ?")
        self.python_status_label.setStyleSheet(default_style)
        self.venv_status_label.setText("Venv: ?")
        self.venv_status_label.setStyleSheet(default_style)
        self.pip_status_label.setText("Pip: ?")
        self.pip_status_label.setStyleSheet(default_style)
        self.packages_status_label.setText("Packages: ?")
        self.packages_status_label.setStyleSheet(default_style)
        self.onnx_status_label.setText("ONNX Runtime: ?")
        self.onnx_status_label.setStyleSheet(default_style)
        self.model_file_status_label.setText("Model File: ?") # Reset new label
        self.model_file_status_label.setStyleSheet(default_style)
        self.tags_file_status_label.setText("Tags File: ?")   # Reset new label
        self.tags_file_status_label.setStyleSheet(default_style)
        self.status_label.setText("Running checks...")

    # --- Button Click Handlers ---

    @pyqtSlot()
    def start_checks(self, is_recheck: bool = False): # Add parameter
        """Initiates the requirement checks in a worker thread."""
        if self.is_checking or self.is_installing:
            return
        self.is_checking = True # Set flag
        if not is_recheck: # Only clear if it's not a recheck after install
            self.status_details_area.clear()
        self.append_status_message("Starting requirement checks...")
        self.set_ui_busy(True)
        self.reset_status_labels()

        # Explicitly pass the dialog's signal emit method as the callback
        worker = Worker(self.run_checks_worker, progress_callback=self.updateStatusSignal.emit)
        # Connect worker signals to dialog slots
        # worker.signals.result.connect(self.checkCompleteSignal.emit) # INCORRECT: Generic worker uses 'finished' for final result
        worker.signals.finished.connect(self.checkCompleteSignal.emit) # CORRECT: Connect finished signal (carrying result dict)
        worker.signals.error.connect(self.handle_worker_error)
        # worker.signals.progress.connect(self.updateStatusSignal.emit) # REMOVED: Progress handled by injected callback

        self.threadpool.start(worker)

    @pyqtSlot()
    def start_installation(self):
        """Initiates the installation process in a worker thread."""
        if self.is_checking or self.is_installing:
            return
        self.is_installing = True # Set flag
        self.append_status_message("\nStarting installation/fix process...")
        self.set_ui_busy(True)

        # Pass necessary info from check_results
        gpu_detected = self.check_results.get("gpu_detected", False)
        # We don't strictly need missing_packages list if we reinstall all from file + onnx
        # missing_packages = self.check_results.get("missing_packages", [])

        # Explicitly pass the dialog's signal emit method as the callback
        worker = Worker(self.run_install_worker, gpu_detected=gpu_detected, progress_callback=self.updateStatusSignal.emit)
        # Connect worker signals
        # Assuming worker returns bool for success, connect finished signal
        # worker.signals.result.connect(lambda success: self.installCompleteSignal.emit(success, "Installation process finished.")) # INCORRECT
        worker.signals.finished.connect(lambda success: self.installCompleteSignal.emit(success, "Installation process finished.")) # CORRECT
        worker.signals.error.connect(self.handle_worker_error)
        # worker.signals.progress.connect(self.updateStatusSignal.emit) # REMOVED: Progress handled by injected callback

        self.threadpool.start(worker)

    # --- Backend Logic (Worker Tasks) ---

    def run_checks_worker(self, progress_callback: pyqtSignal) -> Dict[str, any]:
        """The actual checking logic run by the worker thread."""
        print("DEBUG: run_checks_worker started.") # DEBUG LOG
        results = {
            "python_ok": None,
            "venv_ok": None,
            "pip_ok": None,
            "gpu_detected": None, # Re-add gpu_detected to results
            "onnx_package_needed": None,
            "packages_ok": None,
            "onnx_ok": None, # Status of the onnxruntime *package*
            "model_file_ok": None, # Status of the model.onnx file
            "tags_file_ok": None,  # Status of the selected_tags.csv file
            "missing_packages": [],
            "needs_install": False,
            "overall_ok": False,
        }
        # progress_callback is used directly below

        # --- 1. Check Python Version ---
        progress_callback("--- Checking Python Version ---")
        try:
            min_py_version = (3, 8)
            results["python_ok"] = sys.version_info >= min_py_version
            status = 'OK' if results["python_ok"] else f'FAIL (Requires {min_py_version[0]}.{min_py_version[1]}+)'
            progress_callback(f"Python Version ({platform.python_version()}): {status}")
            if not results["python_ok"]:
                 results["needs_install"] = True
                 return results # Early exit
        except Exception as e:
             progress_callback(f"Python Version Check Error: {e}")
             results["python_ok"] = False
             results["needs_install"] = True
             return results

        # --- 2. Check Venv ---
        progress_callback("--- Checking Virtual Environment ---")
        print("DEBUG: run_checks_worker - Checking venv...") # DEBUG LOG
        venv_python = self._get_venv_python_path()
        if self._check_venv():
            results["venv_ok"] = True
            progress_callback(f"Virtual Env ({VENV_PATH.name}): Found OK")
        else:
            progress_callback(f"Virtual Env ({VENV_PATH.name}): Not found or invalid. Attempting creation...")
            if self._create_venv(progress_callback): # Pass callback for creation output
                results["venv_ok"] = True
                progress_callback("Virtual Env: Creation SUCCESS")
            else:
                results["venv_ok"] = False
                progress_callback("Virtual Env: Creation FAILED (See errors above)")
                results["needs_install"] = True
                print("DEBUG: run_checks_worker - Venv check/creation failed. Returning.") # DEBUG LOG
                return results # Cannot proceed without venv
        print("DEBUG: run_checks_worker - Venv check/creation OK.") # DEBUG LOG

        # --- 3. Check Pip in Venv ---
        progress_callback("--- Checking Pip ---")
        print("DEBUG: run_checks_worker - Checking pip...") # DEBUG LOG
        venv_pip = self._get_venv_pip_path()
        if venv_pip and venv_pip.is_file():
             # Could add a version check here too if needed: pip --version
             results["pip_ok"] = True
             progress_callback(f"Pip ({venv_pip.name}): Found OK")
        else:
             results["pip_ok"] = False
             progress_callback(f"Pip: Not found in venv ({venv_pip}) - Cannot check/install packages.")
             results["needs_install"] = True
             # Attempting to install pip automatically might be complex, flag for install.
             # If pip install fails later, this might be the cause.
             print("DEBUG: run_checks_worker - Pip check failed. Returning.") # DEBUG LOG
             return results # Cannot install packages without pip
        print("DEBUG: run_checks_worker - Pip check OK.") # DEBUG LOG

        # --- 4. GPU Detection ---
        progress_callback("--- Checking for NVIDIA GPU ---")
        print("DEBUG: run_checks_worker - Checking GPU...") # DEBUG LOG
        try:
            cmd = ["nvidia-smi"]
            progress_callback(f"Running: {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=False, shell=platform.system() == "Windows", timeout=10) # Added timeout
            if process.returncode == 0 and "NVIDIA-SMI" in process.stdout:
                 results["gpu_detected"] = True
                 progress_callback("-> NVIDIA GPU Detected.")
            else:
                 results["gpu_detected"] = False
                 progress_callback(f"-> No NVIDIA GPU detected or nvidia-smi failed (Exit Code: {process.returncode}).")
                 if process.stderr: progress_callback(f"   nvidia-smi stderr: {process.stderr.strip()}") # Show stderr on failure
        except FileNotFoundError:
             results["gpu_detected"] = False
             progress_callback("-> nvidia-smi command not found (is NVIDIA driver installed and in PATH?).")
        except subprocess.TimeoutExpired:
             results["gpu_detected"] = False
             progress_callback("-> nvidia-smi command timed out.")
        except Exception as e:
             results["gpu_detected"] = False
             progress_callback(f"-> GPU Check Error: {e}")
        print("DEBUG: run_checks_worker - GPU check finished.") # DEBUG LOG

        # --- 5. Package Check ---
        progress_callback("--- Checking Installed Packages ---")
        print("DEBUG: run_checks_worker - Checking packages...") # DEBUG LOG
        try:
            # Always target onnxruntime-gpu
            results["onnx_package_needed"] = "onnxruntime-gpu"
            progress_callback(f"-> Target ONNX Runtime: GPU (checking for {results['onnx_package_needed']})")

            # Read base requirements from requirements.txt
            base_requirements: Dict[str, str] = {} # { 'package_name_lower': 'Full Specifier Line' }
            if REQ_FILE.is_file():
                progress_callback(f"Reading requirements from: {REQ_FILE}")
                with open(REQ_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                             # More robust parsing for package name needed if complex specifiers are used
                             match = re.match(r"^\s*([a-zA-Z0-9_\-]+)", line)
                             if match:
                                 pkg_name = match.group(1).lower()
                                 # Exclude onnxruntime placeholder from base check list
                                 if "onnxruntime" not in pkg_name:
                                      base_requirements[pkg_name] = line
                             else:
                                 progress_callback(f"Warning: Could not parse requirement line: {line}")
                progress_callback(f"Base requirements found: {list(base_requirements.keys())}")
            else:
                 progress_callback(f"Warning: {REQ_FILE} not found.")
                 # Decide if this is critical - perhaps app can run without it? For now, continue.

            # Get installed packages from venv
            print("DEBUG: run_checks_worker - Calling _get_installed_packages_venv...") # DEBUG LOG
            installed_packages = self._get_installed_packages_venv(progress_callback)
            print(f"DEBUG: run_checks_worker - _get_installed_packages_venv returned: {type(installed_packages)}") # DEBUG LOG
            # --- ADDED LOG ---
            if isinstance(installed_packages, dict):
                progress_callback(f"DEBUG: Installed packages found by pip list: {list(installed_packages.keys())}") # Log keys
            # --- END ADDED LOG ---
            if installed_packages is None: # Check failed
                progress_callback("ERROR: Failed to get installed packages from venv. Cannot verify package status.") # More info
                raise RuntimeError("Failed to get installed packages from venv.")

            # Compare
            all_reqs_found = True
            onnx_found_correctly = False
            missing_list = []

            # Check base requirements
            for req_name, specifier in base_requirements.items():
                 if req_name not in installed_packages:
                     all_reqs_found = False
                     missing_list.append(specifier)
                     progress_callback(f"   MISSING: {specifier}")
                 else:
                     # TODO: Add version comparison using packaging library if needed
                     progress_callback(f"   Found: {req_name} (Version: {installed_packages[req_name]})")

            # Check specific ONNX requirement (always onnxruntime-gpu)
            onnx_req_base_lower = "onnxruntime-gpu"
            min_onnx_version = "1.22.0"
            if onnx_req_base_lower in installed_packages:
                installed_ver = installed_packages[onnx_req_base_lower]
                if installed_ver == min_onnx_version:
                    onnx_found_correctly = True
                    progress_callback(f"   Found: {onnx_req_base_lower} (Version: {installed_ver})")
                else:
                    all_reqs_found = False
                    onnx_found_correctly = False
                    missing_list.append(f"{onnx_req_base_lower}=={min_onnx_version}")
                    progress_callback(f"   FOUND BUT WRONG VERSION: {onnx_req_base_lower} (Version: {installed_ver}) - Requires exactly {min_onnx_version}")
            else:
                all_reqs_found = False # If ONNX is missing, overall packages are not OK
                missing_list.append(f"{onnx_req_base_lower}=={min_onnx_version}") # Add the specific needed one
                progress_callback(f"   MISSING: {onnx_req_base_lower}=={min_onnx_version}")

            results["packages_ok"] = all_reqs_found and onnx_found_correctly # Both base and correct ONNX needed
            results["onnx_ok"] = onnx_found_correctly
            results["missing_packages"] = missing_list
            if not results["packages_ok"]:
                results["needs_install"] = True

        except Exception as e:
             progress_callback(f"Package Check Error: {e}")
             results["packages_ok"] = False
             results["onnx_ok"] = False
             results["needs_install"] = True
             import traceback
             progress_callback(traceback.format_exc()) # Show traceback for package check errors
        print("DEBUG: run_checks_worker - Package check finished.") # DEBUG LOG

        # --- 6. Check Model Files ---
        progress_callback("--- Checking Model Files ---")
        print("DEBUG: run_checks_worker - Checking model files...") # DEBUG LOG
        try:
            # Use the config imported at the module level.
            # Add a check to ensure it's actually loaded, though it should be.
            if 'config' not in sys.modules or not hasattr(config, 'MODEL_PATH'):
                 progress_callback("ERROR: Config module not properly loaded in worker. Cannot check model files.")
                 print("ERROR: Config module not properly loaded in worker.")
                 results["model_file_ok"] = False
                 results["tags_file_ok"] = False
                 results["needs_install"] = True
            else:
                 # Proceed with checks using the module-level config
                 print(f"DEBUG: Checking model path: {config.MODEL_PATH}")
                 results["model_file_ok"] = config.MODEL_PATH.is_file()
            status_model = 'OK' if results["model_file_ok"] else 'FAIL (Missing)'
            progress_callback(f"Model File ({config.MODEL_PATH.name}): {status_model}")

            results["tags_file_ok"] = config.TAGS_CSV_PATH.is_file()
            status_tags = 'OK' if results["tags_file_ok"] else 'FAIL (Missing)'
            progress_callback(f"Tags File ({config.TAGS_CSV_PATH.name}): {status_tags}")

            if not results["model_file_ok"] or not results["tags_file_ok"]:
                results["needs_install"] = True # Mark for install if either file is missing

        except Exception as e:
             progress_callback(f"Model File Check Error: {e}")
             results["model_file_ok"] = False
             results["tags_file_ok"] = False
             results["needs_install"] = True
             import traceback
             progress_callback(traceback.format_exc())
        print("DEBUG: run_checks_worker - Model files check finished.") # DEBUG LOG

        # --- Final Summary ---
        progress_callback("--- Check Summary ---")
        progress_callback(f"Python OK: {results['python_ok']}")
        progress_callback(f"Venv OK: {results['venv_ok']}")
        progress_callback(f"Pip OK: {results['pip_ok']}")
        progress_callback(f"GPU Detected: {results['gpu_detected']}") # Add GPU detected status
        progress_callback(f"Packages OK: {results['packages_ok']}")
        progress_callback(f"ONNX Runtime OK: {results['onnx_ok']}")
        progress_callback(f"Model File OK: {results['model_file_ok']}") # Add model file status
        progress_callback(f"Tags File OK: {results['tags_file_ok']}")   # Add tags file status
        if results["missing_packages"]:
            progress_callback(f"Missing/Incorrect Packages: {', '.join(results['missing_packages'])}")
        if not results.get("model_file_ok", False): # Use .get for safety
             progress_callback(f"Missing File: {config.MODEL_PATH.name}")
        if not results.get("tags_file_ok", False): # Use .get for safety
             progress_callback(f"Missing File: {config.TAGS_CSV_PATH.name}")
        progress_callback(f"Requires Install/Fix: {results['needs_install']}")
        progress_callback("---------------------")

        # Determine overall status
        results["overall_ok"] = (
            results["python_ok"] and
            results["venv_ok"] and
            results["pip_ok"] and
            results["packages_ok"] and # This now includes the correct ONNX check
            results.get("model_file_ok", False) and # Add model file check to overall status
            results.get("tags_file_ok", False)      # Add tags file check to overall status
        )
        progress_callback(f"Overall Requirements Met: {results['overall_ok']}")

        print("DEBUG: run_checks_worker finished. Returning results.") # DEBUG LOG
        return results


    def run_install_worker(self, gpu_detected: bool, progress_callback: pyqtSignal) -> bool:
        """The actual installation logic run by the worker thread."""
        # progress_callback is used directly below
        print("DEBUG: run_install_worker started.") # DEBUG LOG
        progress_callback("--- Starting Installation ---")
        pip_install_ok = True  # Assume OK unless requirements.txt install fails
        model_files_ok = True  # Assume OK unless download fails
        try:
            # Only install requirements.txt packages (ONNX is managed by run.bat)
            progress_callback("--- Installing/Updating Pip Packages (excluding ONNX) ---")
            print("DEBUG: run_install_worker - Getting pip path...") # DEBUG LOG
            venv_pip = self._get_venv_pip_path()
            if not venv_pip or not venv_pip.is_file():
                print("DEBUG: run_install_worker - Pip path check FAILED.") # DEBUG LOG
                raise RuntimeError("Pip not found in venv. Cannot install packages.")
            print(f"DEBUG: run_install_worker - Pip path OK: {venv_pip}") # DEBUG LOG

            # Read base requirements from requirements.txt
            packages_to_install = []
            print(f"DEBUG: run_install_worker - Reading base requirements from {REQ_FILE}...") # DEBUG LOG
            if REQ_FILE.is_file():
                progress_callback(f"Reading base requirements from: {REQ_FILE}")
                with open(REQ_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        line = line.split('#', 1)[0].strip()
                        if line:
                            match = re.match(r"^\s*([a-zA-Z0-9_\-]+)", line)
                            if match: # No longer excluding onnxruntime, as it's now in requirements.txt
                                packages_to_install.append(line)
                            else:
                                progress_callback(f"Warning: Could not parse requirement line for install: {line}")
            print(f"DEBUG: run_install_worker - Packages to install: {packages_to_install}") # DEBUG LOG

            if packages_to_install:
                progress_callback(f"Attempting to install/update: {', '.join(packages_to_install)}")
                cmd = [str(venv_pip), "install", "--no-cache-dir"] + packages_to_install
                progress_callback(f"Running: {' '.join(cmd)}")
                print("DEBUG: run_install_worker - Starting pip install subprocess...")
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding='utf-8', errors='replace',
                                           cwd=config.BASE_DIR, bufsize=1)
                if process.stdout:
                    for line in iter(process.stdout.readline, ''):
                        progress_callback(line.strip())
                if process.stderr:
                    for line in iter(process.stderr.readline, ''):
                        progress_callback(f"PIP ERR: {line.strip()}")
                process.wait()
                print(f"DEBUG: run_install_worker - pip install subprocess finished with code: {process.returncode}")
                if process.returncode != 0:
                    progress_callback(f"--- Pip Installation Failed (Exit Code: {process.returncode}) ---")
                    print("DEBUG: run_install_worker - Pip install failed.")
                    pip_install_ok = False
            else:
                progress_callback("No requirements to install from requirements.txt.")

            # --- 2. Model File Download (if pip install was OK) ---
            if pip_install_ok:
                progress_callback("--- Checking/Downloading Model Files ---")
                # Use the module-level config, but check if it's loaded.
                if 'config' not in sys.modules or not hasattr(config, 'MODELS_DIR'):
                     progress_callback("ERROR: Config module not properly loaded in worker. Cannot download model files.")
                     print("ERROR: Config module not properly loaded in install worker.")
                     model_files_ok = False # Cannot proceed without proper config
                else:
                    # Proceed using the module-level config
                    # Ensure models directory exists using the local_config
                    # Ensure models directory exists using the module-level config
                    try:
                        config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
                        print(f"DEBUG: Ensured models directory exists: {config.MODELS_DIR}")
                    except OSError as e:
                         progress_callback(f"ERROR: Could not create models directory {config.MODELS_DIR}: {e}")
                         print(f"ERROR: Could not create models directory {config.MODELS_DIR}: {e}")
                         model_files_ok = False # Cannot download if dir creation fails

                    # Download Model if missing
                    # Download Model if missing
                    if model_files_ok and not config.MODEL_PATH.is_file():
                        progress_callback(f"Model file '{config.MODEL_PATH.name}' missing. Attempting download...")
                        print(f"DEBUG: Attempting download for {config.MODEL_PATH.name}")
                        model_download_success = self._download_file(
                            MODEL_URL, config.MODEL_PATH, "Model", progress_callback
                        )
                        if not model_download_success:
                            progress_callback(f"ERROR: Failed to download model file.")
                            print(f"ERROR: Failed to download model file {MODEL_URL}")
                            model_files_ok = False # Mark failure
                        else:
                             progress_callback(f"Model file download complete.")
                             print(f"DEBUG: Model file download complete for {config.MODEL_PATH.name}")

                    # Download Tags if missing
                    # Download Tags if missing
                    if model_files_ok and not config.TAGS_CSV_PATH.is_file():
                        progress_callback(f"Tags file '{config.TAGS_CSV_PATH.name}' missing. Attempting download...")
                        print(f"DEBUG: Attempting download for {config.TAGS_CSV_PATH.name}")
                        tags_download_success = self._download_file(
                            TAGS_URL, config.TAGS_CSV_PATH, "Tags", progress_callback
                        )
                        if not tags_download_success:
                            progress_callback(f"ERROR: Failed to download tags file.")
                            print(f"ERROR: Failed to download tags file {TAGS_URL}")
                            model_files_ok = False # Mark failure (overall model files status)
                        else:
                             progress_callback(f"Tags file download complete.")
                             print(f"DEBUG: Tags file download complete for {config.TAGS_CSV_PATH.name}")


            # --- 3. Final Result ---
            overall_success = pip_install_ok and model_files_ok
            if overall_success:
                progress_callback("--- Installation/Download Completed Successfully ---")
                print("DEBUG: run_install_worker finished successfully.")
            else:
                 progress_callback("--- Installation/Download Finished with Errors ---")
                 print("DEBUG: run_install_worker finished with failure (pip or download error).")
            return overall_success

        except Exception as e: # Correctly indented
            progress_callback(f"--- Installation Error: {e} ---")
            import traceback
            progress_callback(traceback.format_exc())
            print("DEBUG: run_install_worker finished with error (exception).") # DEBUG LOG inside except
            return False # Return False on any exception during the process
        # Removed final print statement as return happens within try/except

    def _download_file(self, url: str, target_path: Path, file_description: str, progress_callback: pyqtSignal) -> bool:
        """Downloads a file with progress reporting."""
        # Ensure target directory exists before attempting download
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            progress_callback(f"ERROR: Could not create directory {target_path.parent} for {file_description}: {e}")
            print(f"ERROR: Could not create directory {target_path.parent} for {file_description}: {e}")
            return False

        temp_target_path = target_path.with_suffix(target_path.suffix + '.part')
        print(f"DEBUG: Downloading {file_description} to temporary file: {temp_target_path}")
        try:
            progress_callback(f"Starting download for {file_description} from {url}...")
            # Use a session for potential connection reuse and headers if needed
            with requests.Session() as session:
                # Add a User-Agent header, some servers might block default requests UA
                headers = {'User-Agent': 'ArcExplorer-RequirementsDialog/1.0'}
                response = session.get(url, stream=True, timeout=120, headers=headers) # Increased timeout further, added headers
                response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

                total_size_in_bytes = int(response.headers.get('content-length', 0))
                total_size_mb = total_size_in_bytes / (1024 * 1024) if total_size_in_bytes > 0 else 0
                block_size = 1024 * 1024 # 1MB chunk size for potentially faster downloads?
                downloaded_size = 0
                last_reported_percent = -1
                report_interval_mb = 10 # Report every 10 MB
                next_report_mb = report_interval_mb

                progress_callback(f"Downloading {file_description} ({total_size_mb:.1f} MB)...")

                with open(temp_target_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=block_size):
                        if chunk:  # Filter out keep-alive new chunks
                            file.write(chunk)
                            downloaded_size += len(chunk)
                            downloaded_mb = downloaded_size / (1024 * 1024)

                            if total_size_in_bytes > 0:
                                percent = int(100 * downloaded_size / total_size_in_bytes)
                                # Report progress roughly every 5% or every report_interval_mb, whichever comes first
                                # Ensure we report at least once near the end
                                if percent == 100 or percent >= last_reported_percent + 5 or downloaded_mb >= next_report_mb:
                                    progress_callback(
                                        f"Downloading {file_description}: {downloaded_mb:.1f}/{total_size_mb:.1f} MB ({percent}%)"
                                    )
                                    last_reported_percent = percent
                                    while downloaded_mb >= next_report_mb: # Update next report threshold
                                         next_report_mb += report_interval_mb
                            else:
                                # Report progress in MB if total size is unknown
                                if downloaded_mb >= next_report_mb:
                                     progress_callback(f"Downloading {file_description}: {downloaded_mb:.1f} MB...")
                                     while downloaded_mb >= next_report_mb: # Update next report threshold
                                          next_report_mb += report_interval_mb

                # Rename temporary file to final target path upon successful download
                print(f"DEBUG: Moving temporary file {temp_target_path} to {target_path}")
                shutil.move(str(temp_target_path), str(target_path))
                progress_callback(f"{file_description} download finished successfully.")
                print(f"DEBUG: {file_description} download finished successfully.")
                return True

        except requests.exceptions.RequestException as e:
            progress_callback(f"ERROR downloading {file_description}: Network error - {e}")
            print(f"Network error downloading {url}: {e}")
        except OSError as e:
             progress_callback(f"ERROR saving/moving {file_description}: File system error - {e}")
             print(f"File system error saving/moving {target_path}: {e}")
        except Exception as e:
            progress_callback(f"ERROR during {file_description} download: {e}")
            print(f"Unexpected error downloading {url}: {e}")
            import traceback
            progress_callback(traceback.format_exc())
            print(traceback.format_exc()) # Also print traceback to console for debugging

        # Cleanup temporary file if it exists and an error occurred
        if temp_target_path.exists():
            try:
                print(f"DEBUG: Removing temporary file {temp_target_path}")
                os.remove(temp_target_path) # Use os.remove since temp_target_path is Path object
                progress_callback(f"Cleaned up partial download file: {temp_target_path.name}")
            except OSError as cleanup_e:
                 progress_callback(f"Warning: Could not remove partial download file {temp_target_path.name}: {cleanup_e}")
                 print(f"Warning: Could not remove partial download file {temp_target_path.name}: {cleanup_e}")

        return False


    # --- Helper Methods ---

    def _check_venv(self) -> bool:
        """Checks if the venv path exists and looks like a venv."""
        if not VENV_PATH.is_dir():
            return False
        # Check for key files/dirs (adjust for OS)
        if platform.system() == "Windows":
            python_exe = VENV_PATH / "Scripts" / "python.exe"
            activate_script = VENV_PATH / "Scripts" / "activate"
        else:
            python_exe = VENV_PATH / "bin" / "python"
            activate_script = VENV_PATH / "bin" / "activate"
        config_file = VENV_PATH / "pyvenv.cfg"

        return python_exe.is_file() and activate_script.is_file() and config_file.is_file()

    def _create_venv(self, progress_callback: pyqtSignal) -> bool:
        """Attempts to create the virtual environment."""
        # progress_callback is used directly below
        try:
            # Use the Python executable that's running the app
            python_exe = sys.executable
            if not python_exe:
                progress_callback("ERROR: Could not determine current Python executable path.")
                return False

            cmd = [python_exe, "-m", "venv", str(VENV_PATH)]
            progress_callback(f"Running: {' '.join(cmd)}")
            # Use Popen to stream output during creation
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace',
                                       cwd=config.BASE_DIR, bufsize=1)

            # Stream output
            if process.stdout:
                for line in iter(process.stdout.readline, ''): progress_callback(f"VENV OUT: {line.strip()}")
            stderr_output = ""
            if process.stderr:
                for line in iter(process.stderr.readline, ''):
                     progress_callback(f"VENV ERR: {line.strip()}")
                     stderr_output += line

            process.wait()

            if process.returncode == 0:
                progress_callback("Venv creation command finished.")
                # Verify creation again
                if self._check_venv():
                    return True
                else:
                    progress_callback("ERROR: Venv command succeeded but validation failed.")
                    return False
            else:
                progress_callback(f"Venv creation failed (Exit Code: {process.returncode})")
                # progress_callback(f"Stderr: {stderr_output}") # Already emitted
                return False
        except Exception as e:
            progress_callback(f"Error creating venv: {e}")
            import traceback
            progress_callback(traceback.format_exc())
            return False

    def _get_venv_executable_path(self, executable_name: str) -> Optional[Path]:
        """Gets the path to an executable within the venv's script/bin directory."""
        if platform.system() == "Windows":
            scripts_dir = VENV_PATH / "Scripts"
            exe_with_suffix = f"{executable_name}.exe"
        else:
            scripts_dir = VENV_PATH / "bin"
            exe_with_suffix = executable_name # No suffix needed usually

        # Check with suffix first (most common on Windows)
        exe_path_suffixed = scripts_dir / exe_with_suffix
        if exe_path_suffixed.is_file():
            return exe_path_suffixed

        # Check without suffix (for non-Windows or if suffix wasn't added on Win)
        exe_path = scripts_dir / executable_name
        if exe_path.is_file():
            return exe_path

        return None # Not found

    def _get_venv_python_path(self) -> Optional[Path]:
        """Gets the path to the python executable in the venv."""
        return self._get_venv_executable_path("python")

    def _get_venv_pip_path(self) -> Optional[Path]:
        """Gets the path to the pip executable in the venv."""
        # Pip might be pip, pip3, pipX.Y
        pip_exe = self._get_venv_executable_path("pip")
        if pip_exe: return pip_exe
        pip_exe = self._get_venv_executable_path("pip3")
        if pip_exe: return pip_exe
        # Could add more specific version checks if needed
        return None


    def _get_installed_packages_venv(self, progress_callback: pyqtSignal) -> Optional[Dict[str, str]]:
        """Runs pip list in the venv and returns a dict of {package_name_lower: version}."""
        # progress_callback is used directly below
        venv_pip = self._get_venv_pip_path()
        if not venv_pip:
            progress_callback("ERROR: Cannot list packages, pip not found in venv.")
            return None
        # Use --format=json for reliable parsing
        # Use --disable-pip-version-check to avoid extra stderr noise
        cmd = [str(venv_pip), "list", "--format=json", "--disable-pip-version-check"]
        # --- Diagnostic Logging ---
        import pprint
        env_snapshot = {k: v for k, v in os.environ.items()}
        progress_callback("=== ENVIRONMENT SNAPSHOT BEFORE PIP LIST ===")
        progress_callback(pprint.pformat(env_snapshot)[:2000] + "..." if len(pprint.pformat(env_snapshot)) > 2000 else pprint.pformat(env_snapshot))
        progress_callback(f"Working directory (cwd): {config.BASE_DIR}")
        progress_callback(f"Full pip command: {' '.join(cmd)}")
        # Optionally, log where pip is found in the environment
        import shutil
        pip_path = shutil.which("pip")
        progress_callback(f"shutil.which('pip') in this environment: {pip_path}")
        # --- End Diagnostic Logging ---
        progress_callback(f"Running: {' '.join(cmd)}")
        # Increased timeout as pip list can sometimes be slow
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, check=True,
                                     cwd=config.BASE_DIR, timeout=60)
            # Check for potential warnings in stderr even if return code is 0
            if process.stderr:
                 progress_callback(f"Pip list stderr (warnings):\n{process.stderr.strip()}")

            raw_pip_list_output = process.stdout
            progress_callback(f"DEBUG: Raw pip list JSON output:\n{raw_pip_list_output[:1000]}...") # Log raw output
            installed = json.loads(raw_pip_list_output)
            # Convert list of dicts to dict of {name_lower: version}
            packages_dict = {pkg['name'].lower(): pkg['version'] for pkg in installed}
            progress_callback(f"Found {len(packages_dict)} packages in venv.")
            return packages_dict
        except subprocess.CalledProcessError as e:
            progress_callback(f"ERROR running pip list (Code: {e.returncode}): {e}")
            progress_callback(f"Stderr: {e.stderr}")
            progress_callback("Attempting fallback: python.exe -m pip list ...")
            # Fallback: try using python.exe -m pip list
            venv_python = self._get_venv_python_path()
            if venv_python:
                fallback_cmd = [str(venv_python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"]
                progress_callback(f"Fallback command: {' '.join(fallback_cmd)}")
                try:
                    fallback_proc = subprocess.run(fallback_cmd, capture_output=True, text=True, check=True,
                                                  cwd=config.BASE_DIR, timeout=60)
                    if fallback_proc.stderr:
                        progress_callback(f"Fallback pip list stderr (warnings):\n{fallback_proc.stderr.strip()}")
                    raw_fallback_output = fallback_proc.stdout
                    progress_callback(f"DEBUG: Fallback raw pip list JSON output:\n{raw_fallback_output[:1000]}...") # Log raw output
                    installed = json.loads(raw_fallback_output)
                    packages_dict = {pkg['name'].lower(): pkg['version'] for pkg in installed}
                    progress_callback(f"Found {len(packages_dict)} packages in venv (fallback).")
                    return packages_dict
                except Exception as fallback_e:
                    progress_callback(f"Fallback python.exe -m pip list failed: {fallback_e}")
                    progress_callback(f"Fallback stderr: {getattr(fallback_e, 'stderr', '')}")
                    return None
            else:
                progress_callback("No venv python found for fallback.")
                return None
        except subprocess.TimeoutExpired:
             progress_callback("ERROR: pip list command timed out.")
             return None
        except json.JSONDecodeError as e:
             progress_callback(f"ERROR parsing pip list output: {e}")
             progress_callback(f"Output was:\n{process.stdout[:500]}...") # Show partial output
             return None
        except Exception as e:
            progress_callback(f"ERROR getting installed packages: {e}")
            import traceback
            progress_callback(traceback.format_exc())
            return None

    def closeEvent(self, event):
        """Ensure threads are cleaned up if dialog is closed prematurely."""
        # This is basic; proper thread management might involve waiting or signaling termination
        print("RequirementsDialog closing.")
        # self.threadpool.clear() # Might abruptly stop workers
        # self.threadpool.waitForDone(1000) # Wait briefly
        super().closeEvent(event)

# Example usage (for testing standalone)
if __name__ == '__main__':
    # Ensure BASE_DIR is set correctly if running standalone
    if 'config' not in sys.modules or not hasattr(config, 'BASE_DIR'):
         print("Setting fallback BASE_DIR for standalone execution.")
         config.BASE_DIR = Path(__file__).resolve().parent.parent
    print(f"Standalone test: BASE_DIR = {config.BASE_DIR}")
    print(f"VENV_PATH = {VENV_PATH}")
    print(f"REQ_FILE = {REQ_FILE}")

    app = QApplication(sys.argv)
    dialog = RequirementsDialog()
    dialog.show()
    sys.exit(app.exec())