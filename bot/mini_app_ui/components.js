export function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

export function panel(title, description) {
  const node = element('section', 'panel empty');
  node.append(element('strong', '', title), element('p', '', description));
  return node;
}

export function action(label, callback, secondary = false) {
  const button = element('button', `button${secondary ? ' secondary' : ''}`, label);
  button.type = 'button';
  button.addEventListener('click', callback);
  return button;
}
