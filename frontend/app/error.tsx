"use client";
export default function Error({ reset }: { reset: () => void }) {
  return (
    <main>
      <section className="panel p-8">
        <h1>Unable to load the incident workspace</h1>
        <p className="muted mb-6">Try loading the console again.</p>
        <button className="button primary" onClick={reset}>
          Retry
        </button>
      </section>
    </main>
  );
}
