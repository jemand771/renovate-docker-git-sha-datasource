{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShellNoCC {
  packages = with pkgs; [
    (python3.withPackages (ps: with ps; [
      fastapi
      httpx
      uvicorn
      pytest
    ]))
    git
    ruff
    ty
  ];
}
