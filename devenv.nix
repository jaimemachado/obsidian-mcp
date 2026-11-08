{ pkgs, ... }:
{
  name = "obsidian-mcp";

  # CI and the production image run 3.12; keep the shell on the same interpreter.
  languages.python.package = pkgs.python312;

  # Stacks come from devenv.stacks.nix, pulled in by devenv.yaml. Put
  # project-specific tools here; machine-only settings go in devenv.local.nix.
  packages = with pkgs; [ ];

  # env.DATABASE_URL = "...";
  # services.postgres.enable = true;
}
