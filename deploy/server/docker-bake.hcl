group "default" {
  targets = ["server"]
}

target "server" {
  context = "../.."
  dockerfile = "deploy/server/Dockerfile"
  tags = ["papagui-server:0.4.4"]
  platforms = ["linux/amd64", "linux/arm64"]
}
