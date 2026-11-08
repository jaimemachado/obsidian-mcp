# managed by baseProject — regenerate with `base link`
{ inputs, ... }:
{
  imports =
    if builtins.pathExists ./devenv.stacks.local.nix then
      [ ./devenv.stacks.local.nix ]
    else
      [
        (inputs.devbase + "/lang/python.nix")
      ];
}
