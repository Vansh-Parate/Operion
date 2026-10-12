"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="min-h-screen bg-[#070a12] flex items-center justify-center p-6 text-gray-200">
      <div className="panel max-w-lg w-full p-8 text-center border border-[#27374d] bg-[#0d121c] shadow-2xl rounded-xl">
        <div className="w-12 h-12 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-400 flex items-center justify-center mx-auto mb-4">
          <AlertTriangle size={24} />
        </div>

        <h1 className="text-lg font-bold text-white mb-2">
          Unable to Load Incident Console
        </h1>

        <p className="text-xs text-gray-400 leading-relaxed mb-6">
          An error occurred while loading the incident workspace data. Please verify console connectivity and retry.
        </p>

        {error?.message && (
          <div className="mb-6 p-3 rounded bg-[#060910] border border-[#1c2738] text-[11px] mono text-rose-300 text-left overflow-x-auto">
            {error.message}
          </div>
        )}

        <button
          type="button"
          className="btn btn-primary mx-auto"
          onClick={reset}
        >
          <RotateCcw size={14} /> Retry Workspace Load
        </button>
      </div>
    </div>
  );
}
