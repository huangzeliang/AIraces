/* C 线 · 周期与字典：编辑回填与表单重置。
   只在本地维护表单状态，不做任何计算；周期编号由后端生成，前端不猜。 */
(function () {
  "use strict";

  var form = document.getElementById("cycle-form");
  if (!form) return;

  var title = document.getElementById("cycle-form-title");
  var idField = document.getElementById("cycle-id");
  var nameField = document.getElementById("cycle-name");
  var typeField = document.getElementById("cycle-type");
  var statusField = document.getElementById("cycle-status");
  var startField = document.getElementById("cycle-start");
  var endField = document.getElementById("cycle-end");
  var noteField = document.getElementById("cycle-note");
  var resetButton = document.getElementById("cycle-reset");

  function clearForm() {
    form.reset();
    idField.value = "";
    if (title) title.textContent = "新建周期";
    nameField.focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll(".cycle-edit"), function (button) {
    button.addEventListener("click", function () {
      idField.value = button.dataset.cycleId || "";
      nameField.value = button.dataset.name || "";
      typeField.value = button.dataset.type || "季度";
      statusField.value = button.dataset.status || "未开始";
      startField.value = button.dataset.start || "";
      endField.value = button.dataset.end || "";
      noteField.value = button.dataset.note || "";
      if (title) title.textContent = "编辑周期 · " + (button.dataset.name || "");
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      nameField.focus();
    });
  });

  if (resetButton) resetButton.addEventListener("click", clearForm);
})();
