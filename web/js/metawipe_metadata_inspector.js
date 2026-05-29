import { app } from "/scripts/app.js";
import { ComfyWidgets } from "/scripts/widgets.js";

app.registerExtension({
  name: "metawipe.metadata_inspector",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "MetaWipeMetadataInspector") return;

    const removeViewerWidgets = (node) => {
      if (!node.widgets) return;
      node.widgets = node.widgets.filter((w) => {
        if (typeof w?.name === "string" && w.name.startsWith("metadata_json_view_")) {
          w.onRemove?.();
          return false;
        }
        return true;
      });
    };

    const addViewerWidget = (node, text) => {
      removeViewerWidgets(node);
      const w = ComfyWidgets["STRING"](
        node,
        "metadata_json_view_0",
        ["STRING", { multiline: true }],
        app
      ).widget;
      if (w.element) {
        w.element.readOnly = true;
        w.element.style.opacity = 0.9;
      }
      w.value = text || "";

      requestAnimationFrame(() => {
        const sz = node.computeSize();
        if (sz[0] < node.size[0]) sz[0] = node.size[0];
        if (sz[1] < node.size[1]) sz[1] = node.size[1];
        node.onResize?.(sz);
        app.graph.setDirtyCanvas(true, false);
      });
    };

    const ensureBaseWidgets = function () {
      if (this.__mwComboWidget) return;

      this.__mwMetadataItems = this.__mwMetadataItems || [];
      this.__mwSelectedIndex = this.__mwSelectedIndex || 0;

      this.__mwComboWidget = this.addWidget(
        "combo",
        "inspect_file",
        "(no metadata yet)",
        (value) => {
          const idx = this.__mwMetadataItems.findIndex((x) => x.label === value);
          if (idx >= 0) {
            this.__mwSelectedIndex = idx;
            const selected = this.__mwMetadataItems[idx] || {};
            addViewerWidget(this, selected.metadata_json || selected.metadata_text || "");
          }
        },
        { values: ["(no metadata yet)"] }
      );
    };

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
      ensureBaseWidgets.call(this);
      addViewerWidget(this, "Run the node to populate metadata JSON.");
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      if (onExecuted) onExecuted.apply(this, arguments);
      ensureBaseWidgets.call(this);

      const fromTopLevel = Array.isArray(message?.metadata_items) ? message.metadata_items : null;
      const fromNestedUI = Array.isArray(message?.ui?.metadata_items) ? message.ui.metadata_items : null;
      const items = fromTopLevel || fromNestedUI || [];
      this.__mwMetadataItems = items;

      const labels = items.length > 0 ? items.map((x) => x.label || "(item)") : ["(no metadata)"];
      this.__mwComboWidget.options.values = labels;

      if (items.length === 0) {
        this.__mwSelectedIndex = 0;
        this.__mwComboWidget.value = labels[0];
        addViewerWidget(this, "No metadata items were returned.");
      } else {
        const maxIdx = items.length - 1;
        this.__mwSelectedIndex = Math.min(this.__mwSelectedIndex || 0, maxIdx);
        const selected = items[this.__mwSelectedIndex] || items[0];
        this.__mwComboWidget.value = selected.label || labels[0];
        addViewerWidget(this, selected.metadata_json || selected.metadata_text || "");
      }

      this.setDirtyCanvas(true, true);
    };
  },
});
