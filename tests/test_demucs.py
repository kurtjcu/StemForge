def main():
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    print("Loading Demucs BagOfModels…")
    bag = get_model("htdemucs")
    print("Bag loaded:", type(bag))

    # Extract the first model
    model = bag.models[0]
    print("Extracted model:", type(model))

    model.eval()

    dummy = torch.randn(1, 2, 44100)
    print("Dummy audio created")

    print("Running apply_model…")
    with torch.no_grad():
        out = apply_model(model, dummy, split=True, overlap=0.25)

    print("apply_model returned:", type(out))
    print("Demucs output shape:", out.shape)


if __name__ == "__main__":
    main()
