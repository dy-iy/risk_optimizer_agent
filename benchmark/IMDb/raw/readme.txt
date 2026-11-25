IMDB Sentiment Classification

https://github.com/weakrules/Denoise-multi-weak-sources/tree/master/rules-noisy-labels/IMDB

# Labels 

"0": "Negative",
"1": "Positive"


# Labeling functions

lfs = [
    expression_nexttime,
    keyword_compare,
    keyword_general,
    keyword_finish,
    keyword_plot
]


# lf - expression_nexttime

expression_nexttime = make_expression_lf(name="expression_nexttime", 
    pre_pos=["will ", " ll ", "would ", " d ", "can t wait to "], 
    expression=[" next time", " again", " rewatch", " anymore", " rewind"])
    



# lf - keyword_compare

keyword_compare = make_keyword_lf(name="keyword_compare", 
    keywords_pos=[], 
    keywords_neg=[" than this", " than the film", " than the movie"])



# lf - keyword_general

keyword_general = make_keyword_lf(name="keyword_general", 
    keywords_pos=["masterpiece", "outstanding", "perfect", "great", "good", "nice", "best", "excellent", "worthy", "awesome", "enjoy", "positive", "pleasant", "wonderful", "amazing", "superb", "fantastic", "marvellous", "fabulous"], 
    keywords_neg=["bad", "worst", "horrible", "awful", "terrible", "crap", "shit", "garbage", "rubbish", "waste"])



# lf - keyword_finish

keyword_finish = make_keyword_lf(name="keyword_finish", 
    keywords_pos=[], 
    keywords_neg=["fast forward", "n t finish"])




# lf - keyword_plot

keyword_plot = make_keyword_lf(name="keyword_plot", 
    keywords_pos=["well written", "absorbing", "attractive", "innovative", "instructive", "interesting", "touching", "moving"], 
    keywords_neg=["to sleep", "fell asleep", "boring", "dull", "plain"])