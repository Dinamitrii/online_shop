(() => {
  const frame = document.getElementById('waybill-pdf');
  const button = document.getElementById('print-waybill');
  const help = document.getElementById('print-help');
  const loaded = () => {button.disabled = false;};
  frame.addEventListener('load', loaded);
  // The iframe may have completed before the deferred script was executed.
  if (frame.contentDocument?.readyState === 'complete' && frame.contentDocument.URL !== 'about:blank') loaded();
  button.addEventListener('click', () => {
    help.textContent = 'Изберете принтер в прозореца за печат. Ако браузърът не го отвори, използвайте иконата на принтер в PDF или „Отвори PDF отделно“.';
    try {frame.contentWindow.focus(); frame.contentWindow.print();}
    catch (_) {help.textContent = 'Използвайте иконата на принтер в PDF прегледа или „Отвори PDF отделно“, после Ctrl+P.';}
  });
})();
