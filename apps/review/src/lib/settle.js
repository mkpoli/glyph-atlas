// A crop shimmers until its pixels arrive. The server may render an image that has already loaded by
// the time the page hydrates, so the state is read from the element rather than from a missed event.
export function settle(image) {
  if (image.complete) return
  image.classList.add('pending')
  const done = () => image.classList.remove('pending')
  image.addEventListener('load', done, { once: true })
  image.addEventListener('error', done, { once: true })
  return { destroy() { image.removeEventListener('load', done); image.removeEventListener('error', done) } }
}
