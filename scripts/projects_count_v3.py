import pathlib

p = pathlib.Path("relay/gui/projects.py")
t = p.read_text(encoding="utf-8")
# Use chr(0xB7) for the middle dot to avoid encoding weirdness
DOT = chr(0xB7)
old_signature = "def _rerender(self):"
new_signature = "def _rerender(self):  # noqa: keep indentation"
old = (
    "    def _rerender(self):\n"
    "        query = self.search_edit.text().strip().casefold()\n"
    "        self.list_widget.clear()\n"
    '        for project in sorted(self.projects, key=lambda row: str(row.get("name") or "").casefold()):\n'
    '            name = str(project.get("name") or project.get("project_id") or "Project")\n'
    "            if query and query not in name.casefold():\n"
    "                continue\n"
    '            version = project.get("version") or 1\n'
    f'            item = QListWidgetItem(f"{{name}} {DOT} v{{int(version)}}")\n'
    '            item.setData(Qt.UserRole, str(project.get("project_id") or ""))\n'
    "            self.list_widget.addItem(item)\n"
)
new = (
    "    def _rerender(self):\n"
    "        query = self.search_edit.text().strip().casefold()\n"
    "        self.list_widget.clear()\n"
    "        visible = 0\n"
    '        for project in sorted(self.projects, key=lambda row: str(row.get("name") or "").casefold()):\n'
    '            name = str(project.get("name") or project.get("project_id") or "Project")\n'
    "            if query and query not in name.casefold():\n"
    "                continue\n"
    '            version = project.get("version") or 1\n'
    f'            item = QListWidgetItem(f"{{name}} {DOT} v{{int(version)}}")\n'
    '            item.setData(Qt.UserRole, str(project.get("project_id") or ""))\n'
    "            self.list_widget.addItem(item)\n"
    "            visible += 1\n"
    "        total = len(self.projects)\n"
    "        if not total:\n"
    '            self.count_label.setText("No registered Projects")\n'
    "        elif query and visible != total:\n"
    '            self.count_label.setText(f"{visible} of {total} projects match")\n'
    "        elif query:\n"
    '            self.count_label.setText(f"{total} projects match")\n'
    "        elif total >= 200:\n"
    '            self.count_label.setText(f"{total} projects (server may have more)")\n'
    "        else:\n"
    '            self.count_label.setText(f"{total} projects")\n'
)
if old in t:
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("updated")
else:
    print("not found")
    # Show the file fragment around _rerender for debugging
    idx = t.find("def _rerender(self):")
    print(repr(t[idx : idx + 700]))
