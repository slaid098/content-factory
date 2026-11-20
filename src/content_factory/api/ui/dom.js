/** Small DOM helpers: every node is built from text nodes, never from HTML. */

/**
 * Create one element.
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
export function el(tag, className = "", text) {
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
 * @param {string} id
 * @returns {HTMLElement}
 */
export function byId(id) {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`Не найден элемент #${id}`);
  }
  return node;
}

/**
 * Show or hide one element without touching its content.
 * @param {HTMLElement} node
 * @param {boolean} visible
 */
export function toggle(node, visible) {
  node.hidden = !visible;
}

/**
 * Show a text message, or hide the node when the text is empty.
 * @param {HTMLElement} node
 * @param {string} text
 */
export function setMessage(node, text) {
  node.textContent = text;
  node.hidden = !text;
}

/**
 * Set the text of one element.
 * @param {HTMLElement} node
 * @param {string} text
 */
export function setText(node, text) {
  node.textContent = text;
}
