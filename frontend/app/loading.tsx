export default function Loading() {
  return (
    <main
      className="loading-shell"
      aria-busy="true"
      aria-label="Loading incident console"
    >
      <div className="skeleton h-16" />
      <div className="skeleton h-32" />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="skeleton h-96" />
        <div className="skeleton h-96" />
      </div>
    </main>
  );
}
