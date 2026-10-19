import { useRoute, type Route } from "./hooks";
import { InstructionsPage } from "./InstructionsPage";
import { QueuePage } from "./QueuePage";

function Page({ route }: Readonly<{ route: Route }>) {
  switch (route.page) {
    case "queue":
      return <QueuePage />;
    case "instructions":
      return <InstructionsPage />;
    case "claim":
      return <h1 className="mono">{route.claimId}</h1>;
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
