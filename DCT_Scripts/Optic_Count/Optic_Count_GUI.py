import tkinter
import os
import threading
import queue
import Define_Optic_Count
import Source_count_Netbox
import demo_auth_ai

files_to_count = []
current_sheet_context = None
session_token = None
downloads_path = os.path.join(os.path.expanduser("~/"), "Downloads")


def get_logo():
    from PIL import Image, ImageTk
    image_path = resource_path(os.path.join("assets", "CoreWeave_Logo.png"))
    cw_logo = Image.open(image_path)
    resized_image = cw_logo.resize((150, 100), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(resized_image)


def resource_path(relative_path):
    import sys
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def setup_readonly(widget):
    """Allow selection and copy but block all keyboard editing."""
    def block_edit(event):
        if event.keysym in ('Left', 'Right', 'Up', 'Down', 'Home', 'End',
                            'Prior', 'Next', 'Shift_L', 'Shift_R',
                            'Control_L', 'Control_R', 'Alt_L', 'Alt_R'):
            return
        if event.state & 0x4:  # Ctrl held — allow Ctrl+C and Ctrl+A
            if event.keysym.lower() in ('c', 'a'):
                return
        return 'break'

    def show_context_menu(event):
        menu = tkinter.Menu(widget, tearoff=0)
        menu.add_command(label="Copy",       command=lambda: widget.event_generate('<<Copy>>'))
        menu.add_command(label="Select All", command=lambda: widget.tag_add('sel', '1.0', 'end'))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def select_all(event=None):
        widget.tag_add('sel', '1.0', 'end')
        return 'break'

    def copy(event=None):
        widget.event_generate('<<Copy>>')
        return 'break'

    widget.bind('<Key>', block_edit)
    widget.bind('<Control-a>', select_all)
    widget.bind('<Command-a>', select_all)
    widget.bind('<Control-c>', copy)
    widget.bind('<Command-c>', copy)
    widget.bind('<Button-2>', show_context_menu)
    widget.bind('<Button-3>', show_context_menu)


def browse_file_on_click():
    from tkinter import filedialog
    filename = filedialog.askopenfilename(
        title="Select a file",
        initialdir=downloads_path,
        filetypes=(('Excel files', '*.xlsx'), ('All files', '*.*')))
    if filename:
        entry_field.delete(0, tkinter.END)
        entry_field.insert(0, filename)
        main_window.focus_force()
        entry_field.focus_set()
        on_button_add_click()


def on_button_add_click(event=None):
    from tkinter import messagebox
    filepath = entry_field.get()
    if not filepath.lower().endswith(".xlsx"):
        messagebox.showerror("File Format Error", "File not added!\nMust be in .xlsx format.")
        main_window.focus_force()
        entry_field.focus_set()
    else:
        files_to_count.append(filepath)
        files_text.insert(tkinter.END, f"{filepath}\n")
    entry_field.delete(0, 'end')


def _format_device_models(context):
    models = context.get("device_model_summary", {})
    if not models:
        return ""
    lines = ["\nDevice Models:"]
    for model, info in sorted(models.items(), key=lambda x: x[1]["count"], reverse=True):
        locs = ", ".join(list(info.get("locations", {}).keys())[:5])
        loc_str = f" @ {locs}" if locs else ""
        lines.append(f"  {model}: {info['count']} (A:{info['a_side_count']} Z:{info['z_side_count']}){loc_str}")
    return "\n".join(lines)


def _format_location_assets(context):
    loc_index = context.get("cutsheet_device_model_index", {})
    if not loc_index:
        return ""
    lines = ["\nAssets by Location:"]
    sorted_locs = sorted(
        loc_index.items(),
        key=lambda x: sum(x[1].get("models", {}).values()),
        reverse=True,
    )
    for loc, info in sorted_locs[:30]:
        model_list = ", ".join(f"{m} x{c}" for m, c in info.get("models", {}).items())
        lines.append(f"  {loc}: {model_list}")
    return "\n".join(lines)


def on_button_count_click():
    global current_sheet_context
    from tkinter import messagebox
    data_text.delete("1.0", 'end')

    if not files_to_count:
        messagebox.showwarning("No Files", "No files loaded. Please add a file first.")
        return

    try:
        data_to_print = Define_Optic_Count.count_all_files_gui(files_to_count)
        current_sheet_context = Define_Optic_Count.build_sheet_context(files_to_count)
        data_text.insert(tkinter.END, data_to_print)
    except (FileNotFoundError, ValueError) as e:
        data_text.insert(tkinter.END, f"Error during count:\n{e}")


def _stream_to_text(output_queue, reset_button, reset_label):
    def poll_queue():
        while True:
            try:
                msg = output_queue.get_nowait()
            except queue.Empty:
                main_window.after(100, poll_queue)
                return
            if msg is None:
                reset_button.config(state='normal', text=reset_label)
                return
            data_text.insert(tkinter.END, msg)
            data_text.see(tkinter.END)
    main_window.after(100, poll_queue)


def on_button_netbox_click():
    site_name = netbox_entry_field.get().strip() or "US-WEST-09A"
    button_netbox.config(state='disabled', text="Loading...")
    data_text.delete("1.0", 'end')
    output_queue = queue.Queue()
    threading.Thread(
        target=Source_count_Netbox.get_site_inventory,
        args=(site_name, output_queue),
        kwargs={"active_only": netbox_active_only_var.get(), "include_optic_locations": netbox_optic_locations_var.get()},
        daemon=True
    ).start()
    _stream_to_text(output_queue, button_netbox, "Netbox")


def on_button_all_sites_click():
    button_all_sites.config(state='disabled', text="Loading...")
    data_text.delete("1.0", 'end')
    output_queue = queue.Queue()
    threading.Thread(
        target=Source_count_Netbox.get_all_sites_inventory,
        args=(output_queue,),
        kwargs={"active_only": netbox_active_only_var.get(), "include_optic_locations": netbox_optic_locations_var.get()},
        daemon=True
    ).start()
    _stream_to_text(output_queue, button_all_sites, "All Sites")


def on_button_count_by_status_click():
    from tkinter import messagebox
    data_text.delete("1.0", 'end')

    if not files_to_count:
        messagebox.showwarning("No Files", "No files loaded. Please add a file first.")
        return

    try:
        data_to_print = Define_Optic_Count.count_all_files_gui_by_status(files_to_count)
        data_text.insert(tkinter.END, data_to_print)
    except (FileNotFoundError, ValueError) as e:
        data_text.insert(tkinter.END, f"Error during count:\n{e}")


def on_button_build_status_click():
    from tkinter import messagebox
    if not files_to_count:
        messagebox.showwarning("No Files", "No files loaded. Please add a file first.")
        return
    data_text.delete("1.0", 'end')
    try:
        result = Define_Optic_Count.count_all_files_build_status_gui(files_to_count)
        data_text.insert(tkinter.END, result)
    except (FileNotFoundError, ValueError) as e:
        data_text.insert(tkinter.END, f"Error during build status report:\n{e}")


def on_button_clear_click():
    global files_to_count, current_sheet_context, session_token
    files_to_count = []
    current_sheet_context = None
    session_token = None
    files_text.delete("1.0", 'end')
    verify_status_var.set("Not verified")
    qa_output.config(state='normal')
    qa_output.delete("1.0", 'end')
    qa_output.config(state='disabled')


def on_verify_pin_click():
    global session_token
    from tkinter import messagebox

    username = username_entry.get().strip() or "demo_user"
    pin = pin_entry.get().strip()
    if not pin:
        messagebox.showerror("PIN Required", "Please enter your demo PIN.")
        return

    if not demo_auth_ai.verify_demo_pin(pin):
        verify_status_var.set("Verification failed")
        messagebox.showerror("Verification Failed", "Invalid demo PIN.")
        return

    session_token = demo_auth_ai.create_demo_token(username)
    verify_status_var.set(f"Verified as {username}")
    messagebox.showinfo("Verified", "Demo verification successful. You can now ask grounded AI questions.")


def on_ask_ai_click():
    from tkinter import messagebox
    import datetime

    if not current_sheet_context:
        messagebox.showerror("No Sheet Context", "Please count optics first so sheet context is available.")
        return
    if not session_token:
        messagebox.showerror("Not Verified", "Please verify PIN before AI Q&A.")
        return

    question = qa_entry.get().strip()
    if not question:
        messagebox.showerror("Question Required", "Enter a question for AI.")
        return

    try:
        result = demo_auth_ai.qa_with_token(session_token, question, current_sheet_context)
    except Exception as exc:  # noqa: BLE001
        messagebox.showerror("Q&A Error", str(exc))
        return

    provider = result.get("provider", "")
    model = result.get("model", "")
    in_tok = result.get("input_tokens", 0)
    out_tok = result.get("output_tokens", 0)
    elapsed = result.get("elapsed_seconds", 0)
    user = result.get("user", "")
    ts = result.get("timestamp", 0)
    time_str = datetime.datetime.fromtimestamp(ts).strftime("%m/%d/%Y, %I:%M:%S %p") if ts else ""

    header = f"User: {user}\nTime: {time_str}\n"
    if provider:
        header += f"{provider} / {model}  |  {in_tok:,} in + {out_tok:,} out tokens  |  {elapsed}s\n"
    header += "\n"

    qa_output.config(state='normal')
    qa_output.delete("1.0", 'end')
    qa_output.insert(tkinter.END, header + result["answer"])
    qa_output.config(state='disabled')


main_window = tkinter.Tk()
main_window.title("Atlas - DCT Infrastructure Intelligence")
main_window.geometry("1000x900")

# Scrollable window setup — grid so scrollbar can hide without disrupting layout
main_window.grid_rowconfigure(0, weight=1)
main_window.grid_columnconfigure(0, weight=1)

scroll_canvas = tkinter.Canvas(main_window)
scrollbar = tkinter.Scrollbar(main_window, orient="vertical", command=scroll_canvas.yview)
scroll_canvas.grid(row=0, column=0, sticky="nsew")
scrollbar.grid(row=0, column=1, sticky="ns")

inner_frame = tkinter.Frame(scroll_canvas)
canvas_window = scroll_canvas.create_window((0, 0), window=inner_frame, anchor="nw")


def _update_scrollbar(first, last):
    """Show scrollbar only when content exceeds the visible area."""
    scrollbar.set(first, last)
    if float(first) == 0.0 and float(last) == 1.0:
        scrollbar.grid_remove()
    else:
        scrollbar.grid(row=0, column=1, sticky="ns")


def _on_inner_frame_configure(event):
    scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))


def _on_canvas_configure(event):
    scroll_canvas.itemconfig(canvas_window, width=event.width)


def _on_mousewheel(event):
    # Let Text widgets handle their own scrolling
    if isinstance(event.widget, tkinter.Text):
        return
    # Only scroll if content actually exceeds the visible area
    if scroll_canvas.yview() != (0.0, 1.0):
        scroll_canvas.yview_scroll(int(-1 * event.delta), "units")


scroll_canvas.configure(yscrollcommand=_update_scrollbar)
inner_frame.bind("<Configure>", _on_inner_frame_configure)
scroll_canvas.bind("<Configure>", _on_canvas_configure)
scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

# Entry Frame
entry_frame = tkinter.Frame(inner_frame)
entry_frame.pack(pady=10)
entry_label = tkinter.Label(entry_frame, text="Select File", font=("Arial", 20, "bold"))
entry_field = tkinter.Entry(entry_frame, width=40)
button_browse = tkinter.Button(entry_frame, text="Browse", command=browse_file_on_click)
entry_label.pack(side=tkinter.LEFT, padx=5)
entry_field.pack(side=tkinter.LEFT, padx=5)
button_browse.pack(side=tkinter.LEFT, padx=5)

# Button Frame
button_frame = tkinter.Frame(inner_frame)
button_frame.pack(pady=10)
button_clear_files = tkinter.Button(button_frame, text="Clear Files", command=on_button_clear_click)
button_clear_files.pack(side=tkinter.RIGHT, padx=20)

entry_field.focus_set()
main_window.bind('<Return>', on_button_add_click)

# Content Frame — shared container so buttons match text widget width
content_frame = tkinter.Frame(inner_frame)
content_frame.pack(pady=5)

# File Frame
files_frame = tkinter.Frame(content_frame)
files_frame.pack()
files_label = tkinter.Label(files_frame, text="Loaded Files", font=("Arial", 12, "bold"))
files_text = tkinter.Text(files_frame, height=10)
setup_readonly(files_text)
files_label.pack(side=tkinter.TOP)
files_text.pack(side=tkinter.TOP)

# Netbox Frame
netbox_frame = tkinter.Frame(content_frame)
netbox_label = tkinter.Label(netbox_frame, text="NetBox Site:", font=("Arial", 11))
netbox_entry_field = tkinter.Entry(netbox_frame, width=50)
netbox_active_only_var = tkinter.BooleanVar(value=True)
netbox_active_only_check = tkinter.Checkbutton(netbox_frame, text="Count In Service items only", variable=netbox_active_only_var)
netbox_optic_locations_var = tkinter.BooleanVar(value=False)
netbox_optic_locations_check = tkinter.Checkbutton(netbox_frame, text="Include itemized optic locations", variable=netbox_optic_locations_var)
netbox_label.pack(side=tkinter.LEFT, padx=5)
netbox_entry_field.pack(side=tkinter.LEFT, padx=5)
netbox_active_only_check.pack(side=tkinter.LEFT, padx=10)
netbox_optic_locations_check.pack(side=tkinter.LEFT, padx=10)
netbox_frame.pack(pady=5)

# Count Buttons
count_buttons_frame = tkinter.Frame(content_frame)
button_count_optics = tkinter.Button(count_buttons_frame, text="Count", command=on_button_count_click)
button_count_by_status = tkinter.Button(count_buttons_frame, text="Count, Sort by In Service", command=on_button_count_by_status_click)
button_netbox = tkinter.Button(count_buttons_frame, text="Netbox", command=on_button_netbox_click, width=20)
button_all_sites = tkinter.Button(count_buttons_frame, text="All Sites", command=on_button_all_sites_click, width=20)
button_build_status = tkinter.Button(count_buttons_frame, text="Build Status Report", command=on_button_build_status_click)

count_buttons_frame.pack(fill=tkinter.X, pady=5)
button_count_optics.pack(side=tkinter.LEFT, fill=tkinter.X, expand=True)
button_count_by_status.pack(side=tkinter.LEFT, fill=tkinter.X, expand=True)
button_netbox.pack(side=tkinter.LEFT, padx=5, expand=True)
button_all_sites.pack(side=tkinter.LEFT, padx=5, expand=True)
button_build_status.pack(side=tkinter.LEFT, fill=tkinter.X, expand=True)

data_text = tkinter.Text(content_frame)
setup_readonly(data_text)
data_text.pack(fill=tkinter.X)

# Verify Frame
verify_frame = tkinter.LabelFrame(inner_frame, text="Demo Verify (Simulated Okta Verify)")
verify_frame.pack(fill="x", padx=10, pady=10)

tkinter.Label(verify_frame, text="Username").pack(side=tkinter.LEFT, padx=5)
username_entry = tkinter.Entry(verify_frame, width=20)
username_entry.insert(0, "demo_user")
username_entry.pack(side=tkinter.LEFT, padx=5)

tkinter.Label(verify_frame, text="PIN").pack(side=tkinter.LEFT, padx=5)
pin_entry = tkinter.Entry(verify_frame, width=12, show="*")
pin_entry.pack(side=tkinter.LEFT, padx=5)

verify_button = tkinter.Button(verify_frame, text="Verify Identity", command=on_verify_pin_click)
verify_button.pack(side=tkinter.LEFT, padx=10)

verify_status_var = tkinter.StringVar(value="Not verified")
verify_status_label = tkinter.Label(verify_frame, textvariable=verify_status_var)
verify_status_label.pack(side=tkinter.LEFT, padx=10)

# AI Q&A Frame
qa_frame = tkinter.LabelFrame(inner_frame, text="Ask Atlas (Sheet Context Only)")
qa_frame.pack(fill="both", expand=True, padx=10, pady=10)

tkinter.Label(qa_frame, text="Question").pack(anchor="w", padx=5)
qa_entry = tkinter.Entry(qa_frame, width=140)
qa_entry.pack(fill="x", padx=5, pady=5)

qa_button = tkinter.Button(qa_frame, text="Ask AI", command=on_ask_ai_click)
qa_button.pack(anchor="w", padx=5, pady=5)

qa_output = tkinter.Text(qa_frame, height=12, width=120, state="disabled")
setup_readonly(qa_output)
qa_output.pack(fill="both", expand=True, padx=5, pady=5)

"""LOGO"""
try:
    CW_logo = get_logo()
    CW_logo_label = tkinter.Label(inner_frame, image=CW_logo)
except (FileNotFoundError, OSError):
    CW_logo = None
    CW_logo_label = tkinter.Label(inner_frame, text="")
"""end logo"""
CW_logo_label.pack(padx=5)
