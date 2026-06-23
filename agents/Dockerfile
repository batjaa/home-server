FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGET=movie-agent
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags "-s -w" -o /out/agent ./cmd/${TARGET}

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/agent /agent
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/agent"]
