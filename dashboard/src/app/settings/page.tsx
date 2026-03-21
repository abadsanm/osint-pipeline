import Header from "@/components/Header";

export default function SettingsPage() {
  return (
    <div className="flex flex-col h-screen">
      <Header title="Settings" />
      <div className="flex-1 p-module-gap-lg">
        <div className="card-lg max-w-2xl">
          <h2 className="text-lg font-semibold mb-4">Configuration</h2>
          <p className="text-text-secondary text-sm">
            Settings and configuration options will be available here.
            Data source connections, alert thresholds, and display preferences.
          </p>
        </div>
      </div>
    </div>
  );
}
