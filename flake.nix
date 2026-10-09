{
  description = "A digest of nixpkgs' open pull requests, sorted by what they change, for nixkeeper.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # nixkeeper's code for ordering versions (versions.py: Repology's
    # libversion, with nixpkgs' "unstable"), so an update PR is superseded
    # or overtaken exactly when nixkeeper would say so.
    nixkeeper = {
      url = "github:iedame/nixkeeper";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        flake-utils.follows = "flake-utils";
        treefmt-nix.follows = "treefmt-nix";
        nix-darwin.inputs.nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      treefmt-nix,
      nixkeeper,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        inherit (pkgs) lib;

        # The code and its tests, run with nixkeeper's source beside them,
        # with brotli for the channel's package index. The rest needs the
        # standard library only.
        src = lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./nixkeeper_prs
            ./tests
          ];
        };
        python = pkgs.python3.withPackages (ps: [ ps.brotli ]);
        pythonPath = "${src}:${nixkeeper}";

        # `nix fmt` formats everything; checks.formatting fails on anything
        # unformatted. ruff's settings live in pyproject.toml, biome's in
        # biome.json (nixkeeper's).
        treefmt = treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";
          programs = {
            nixfmt.enable = true;
            ruff-format.enable = true;
            ruff-check.enable = true; # safe auto-fixes, e.g. import order
            biome = {
              enable = true;
              settings = builtins.fromJSON (builtins.readFile ./biome.json);
            };
          };
          settings.formatter.biome.includes = [
            "page/*.js"
            "page/*.css"
          ];
        };

        linters = with pkgs; [
          ruff
          deadnix
          statix
          actionlint
          shellcheck # used by actionlint for the workflows' run: scripts
          biome
        ];
      in
      {
        formatter = treefmt.config.build.wrapper;

        # `nix run`: writes the digest to ./data (or the folder given), as
        # the workflow does. Needs GITHUB_TOKEN (GitHub's GraphQL API).
        apps.default = {
          type = "app";
          program = lib.getExe (
            pkgs.writeShellScriptBin "nixkeeper-prs" ''
              PYTHONPATH=${pythonPath} exec ${lib.getExe python} -m nixkeeper_prs "$@"
            ''
          );
          meta.description = "Write the digest of nixpkgs' open pull requests";
        };

        checks = {
          tests = pkgs.runCommand "nixkeeper-prs-tests" { nativeBuildInputs = [ python ]; } ''
            cd ${src}
            PYTHONPATH=${pythonPath} python3 -m unittest discover -s tests -t . -v
            touch $out
          '';
          formatting = treefmt.config.build.check self;
          lint = pkgs.runCommand "nixkeeper-prs-lint" { nativeBuildInputs = linters; } ''
            cd ${self}
            export HOME=$TMPDIR
            ruff check --no-cache .
            deadnix --fail .
            statix check .
            # Named explicitly: on its own actionlint looks for .git, which the
            # flake source (CI's view of the repo) doesn't include.
            actionlint .github/workflows/*.yml
            shellcheck scripts/*.sh
            biome lint page
            touch $out
          '';
        };

        devShells.default = pkgs.mkShell {
          packages = [
            python
            treefmt.config.build.wrapper
          ]
          ++ linters;
          # nixkeeper's source, as the app and the tests have it.
          shellHook = "export PYTHONPATH=${nixkeeper}\${PYTHONPATH:+:$PYTHONPATH}";
        };
      }
    );
}
