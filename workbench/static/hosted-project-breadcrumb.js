/* Shared breadcrumb. Scope comes only from the user's explicit project/XML selection. */
(() => {
  'use strict';
  const page = document.getElementById('page-name');
  if (!page) return;

  const project = document.createElement('span');
  const projectSeparator = document.createElement('span');
  const packageName = document.createElement('span');
  const packageSeparator = document.createElement('span');
  project.id = 'hosted-project-breadcrumb';
  packageName.id = 'selected-package-breadcrumb';
  projectSeparator.textContent = '/';
  packageSeparator.textContent = '/';
  projectSeparator.setAttribute('aria-hidden', 'true');
  packageSeparator.setAttribute('aria-hidden', 'true');
  page.before(project, projectSeparator, packageName, packageSeparator);

  function update() {
    const picker = document.getElementById('estate-package-select');
    const name = picker?.dataset.projectId ? picker.dataset.projectName : '';
    const selected = picker?.value ? picker.selectedOptions?.[0] : null;
    const selectedPackage = selected?.dataset.packageName || '';
    if (project.textContent !== (name || '')) project.textContent = name || '';
    if (packageName.textContent !== selectedPackage) packageName.textContent = selectedPackage;
    project.hidden = !name;
    projectSeparator.hidden = !name;
    packageName.hidden = !selectedPackage;
    packageSeparator.hidden = !name || !selectedPackage;
  }
  document.addEventListener('change', (event) => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target?.id)) update();
  });
  const picker = document.getElementById('estate-package-select');
  if (picker) {
    new MutationObserver(update).observe(picker, {
      attributes: true,
      attributeFilter: ['data-project-id', 'data-project-name'],
      childList: true,
      subtree: true,
    });
  }
  update();
})();