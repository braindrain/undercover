import { ClaimPage } from "./ClaimPage";
import { EvaluationPage } from "./EvaluationPage";
import { useRoute, type Route } from "./hooks";
import { InstructionsPage } from "./InstructionsPage";
import { QueuePage } from "./QueuePage";
import { ScoreboardPage } from "./ScoreboardPage";

function Page({ route }: Readonly<{ route: Route }>) {
  switch (route.page) {
    case "queue":
      return <QueuePage />;
    case "instructions":
      return <InstructionsPage />;
    case "scoreboard":
      return <ScoreboardPage />;
    case "evaluation":
      return <EvaluationPage key={route.runId} runId={route.runId} />;
    case "claim":
      return <ClaimPage key={route.claimId} claimId={route.claimId} />;
  }
}

export function App() {
  const route = useRoute();
  const current = (page: Route["page"]) =>
    route.page === page ? "page" : undefined;

  return (
    <>
      <header className="header">
        <a className="brand" href="#/">
          UnderCover<span className="tagline">Claims desk</span>
        </a>
        <nav className="nav" aria-label="Main">
          <a href="#/" aria-current={current("queue") ?? current("claim")}>
            Queue
          </a>
          <a
            href="#/scoreboard"
            aria-current={current("scoreboard") ?? current("evaluation")}
          >
            Scoreboard
          </a>
          <a href="#/instructions" aria-current={current("instructions")}>
            Instructions
          </a>
        </nav>
      </header>
      <main className="main">
        <Page route={route} />
      </main>
    </>
  );
}
