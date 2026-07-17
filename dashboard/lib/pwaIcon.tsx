import { ImageResponse } from "next/og";

export function renderPwaIcon(size: number): ImageResponse {
  return new ImageResponse(
    (
      <div
        style={{
          alignItems: "center",
          background: "#000000",
          display: "flex",
          height: "100%",
          justifyContent: "center",
          width: "100%"
        }}
      >
        <div
          style={{
            alignItems: "center",
            background: "#00c805",
            borderRadius: "50%",
            display: "flex",
            height: "68%",
            justifyContent: "center",
            width: "68%"
          }}
        >
          <svg aria-hidden="true" height="58%" viewBox="0 0 100 100" width="58%">
            <path
              d="M5 53h20l9-28 15 54 13-44 9 18h24"
              fill="none"
              stroke="#000000"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="10"
            />
          </svg>
        </div>
      </div>
    ),
    { height: size, width: size }
  );
}
