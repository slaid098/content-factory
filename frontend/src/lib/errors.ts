/** Turn an unknown thrown value into the text the page shows. */

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
