const report = document.getElementById('report');
const discussion = document.getElementById('discussion');
const printButton = document.getElementById('print');

function syncView() {
  const inDiscussion = location.hash === '#discussion';
  report.hidden = inDiscussion;
  discussion.hidden = !inDiscussion;
  if (!inDiscussion) {
    const target = document.getElementById(location.hash.slice(1));
    const detail = target?.querySelector(':scope > details');
    if (detail && target.id.startsWith('need-')) detail.open = true;
    if (target) target.scrollIntoView?.();
  }
}
window.addEventListener('hashchange', syncView);
syncView();

document.querySelectorAll('[data-switch]').forEach(group => {
  const buttons = [...group.querySelectorAll(':scope > button[aria-controls]')];
  function select(button) {
    for (const item of buttons) {
      const selected = item === button;
      item.setAttribute('aria-pressed', String(selected));
      document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
    }
  }
  if (buttons.length) {
    select(buttons.find(button => button.hasAttribute('data-default')) || buttons[0]);
    for (const button of buttons) button.addEventListener('click', () => select(button));
  }
});

printButton.hidden = false;
printButton.addEventListener('click', () => window.print());
let folded = [];
window.addEventListener('beforeprint', () => {
  folded = [...report.querySelectorAll('details:not([open])')];
  folded.forEach(item => item.open = true);
});
window.addEventListener('afterprint', () => {
  folded.forEach(item => item.open = false);
  folded = [];
});
