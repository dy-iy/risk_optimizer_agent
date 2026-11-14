Yelp Sentiment Classification
https://github.com/weakrules/Denoise-multi-weak-sources/tree/master/rules-noisy-labels/Yelp


# Labels

"0": "Negative",
"1": "Positive"


# Labeling functions

lfs = [
    textblob_lf,
    keyword_recommend,
    keyword_general,
    keyword_mood,
    keyword_service,
    keyword_price,
    keyword_environment,
    keyword_food,
]



# lf - textblob_lf

@preprocessor(memoize=True)
def textblob_sentiment(x):
    scores = TextBlob(x.text)
    x.polarity = scores.sentiment.polarity
    x.subjectivity = scores.sentiment.subjectivity
    return x

@labeling_function(pre=[textblob_sentiment])
def textblob_lf(x):
    if x.polarity < -0.5:
        return NEG
    if x.polarity > 0.5:
        return POS
    return ABSTAIN



# lf - keyword_recommend

keyword_recommend = make_keyword_lf(name="keyword_recommend", 
    keywords_pos=["recommend"], 
    keywords_neg=[])



# lf - keyword_general

keyword_general = make_keyword_lf(name="keyword_general", 
    keywords_pos=["outstanding", "perfect", "great", "good", "nice", "best", "excellent", "worthy", "awesome", "enjoy", "positive", "pleasant", "wonderful", "amazing"], 
    keywords_neg=["bad", "worst", "horrible", "awful", "terrible", "nasty", "shit", "distasteful", "dreadful", "negative"])



# lf - keyword_mood

keyword_mood = make_keyword_lf(name="keyword_mood", 
    keywords_pos=["happy", "pleased", "delighted", "contented", "glad", "thankful", "satisfied"], 
    keywords_neg=["sad", "annoy", "disappointed", "frustrated", "upset", "irritated", "harassed", "angry", "pissed"])



# lf - keyword_service

keyword_service = make_keyword_lf(name="keyword_service", 
    keywords_pos=["friendly", "patient", "considerate", "enthusiastic", "attentive", "thoughtful", "kind", "caring", "helpful", "polite", "efficient", "prompt"], 
    keywords_neg=["slow", "offended", "rude", "indifferent", "arrogant"])



# lf - keyword_price

keyword_price = make_keyword_lf(name="keyword_price", 
    keywords_pos=["cheap", "reasonable", "inexpensive", "economical"], 
    keywords_neg=["overpriced", "expensive", "costly", "high-priced"])




# lf - keyword_environment

keyword_environment = make_keyword_lf(name="keyword_environment", 
    keywords_pos=["clean", "neat", "quiet", "comfortable", "convenien", "tidy", "orderly", "cosy", "homely"], 
    keywords_neg=["noisy", "mess", "chaos", "dirty", "foul"])



# lf - keyword_food

keyword_food = make_keyword_lf(name="keyword_food", 
    keywords_pos=["tasty", "yummy", "delicious", "appetizing", "good-tasting", "delectable", "savoury", "luscious", "palatable"], 
    keywords_neg=["disgusting", "gross", "insipid"])




