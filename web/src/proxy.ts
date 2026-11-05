import { NextResponse, type NextRequest } from "next/server";

// Optimistic check only: the API validates the session on every request.
export function proxy(request: NextRequest) {
  const signedIn = request.cookies.has("ymva_session");
  const { pathname, search } = request.nextUrl;
  if (pathname === "/integration") return NextResponse.next(); // public API guide
  if (pathname === "/login") {
    return signedIn ? NextResponse.redirect(new URL("/", request.url)) : NextResponse.next();
  }
  if (!signedIn) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|svg|ico|webp|woff2?|tgz)$).*)"],
};
