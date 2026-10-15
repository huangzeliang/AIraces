/* C 线 · 组织与角色：部门表单编辑回填。
   只在本地维护表单状态，不做任何计算；达成度一律由后端口径引擎给出。 */
(function () {
  "use strict";

  var form = document.getElementById("dept-form");
  if (!form) return;

  var title = document.getElementById("dept-form-title");
  var idField = document.getElementById("dept-id");
  var nameField = document.getElementById("dept-name");
  var parentField = document.getElementById("dept-parent");
  var leaderField = document.getElementById("dept-leader");
  var noteField = document.getElementById("dept-note");
  var resetButton = document.getElementById("dept-reset");

  function clearForm() {
    form.reset();
    idField.value = "";
    if (title) title.textContent = "新建部门";
    nameField.focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll(".dept-edit"), function (button) {
    button.addEventListener("click", function () {
      idField.value = button.dataset.deptId || "";
      nameField.value = button.dataset.name || "";
      parentField.value = button.dataset.parent || "";
      leaderField.value = button.dataset.leader || "";
      noteField.value = button.dataset.note || "";
      // 编辑时不能把自己选成上级，避免提交后才被后端拒绝
      Array.prototype.forEach.call(parentField.options, function (option) {
        option.disabled = option.value === idField.value;
      });
      if (title) title.textContent = "编辑部门 · " + (button.dataset.name || "");
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      nameField.focus();
    });
  });

  if (resetButton) {
    resetButton.addEventListener("click", function () {
      clearForm();
      Array.prototype.forEach.call(parentField.options, function (option) {
        option.disabled = false;
      });
    });
  }
})();
