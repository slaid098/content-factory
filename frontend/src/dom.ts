/** Small DOM helpers: every node is built from text nodes, never from HTML. */

/**
 * Create one element.
 */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

/**
 * Find a required element by id.
 */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`Не найден элемент #${id}`);
  }
  return node as T;
}

/**
 * Show or hide one element without touching its content.
 */
export function toggle(node: HTMLElement, visible: boolean): void {
  node.hidden = !visible;
}

/**
 * Show a text message, or hide the node when the text is empty.
 */
export function setMessage(node: HTMLElement, text: string): void {
  node.textContent = text;
  node.hidden = !text;
}

/**
 * Set the text of one element.
 */
export function setText(node: HTMLElement, text: string): void {
  node.textContent = text;
}
