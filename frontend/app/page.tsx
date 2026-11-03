import Link from "next/link";

export default function Home() {
  return (
    <main>
      <h1>Interview scoring</h1>
      <p>Candidates: please open the personal link you were emailed.</p>
      <p><Link href="/hr/login">HR sign-in</Link></p>
    </main>
  );
}
