import spacy

nlp = spacy.blank("en")
doc = nlp("Hello world")

print("Tokens:", [t.text for t in doc])
