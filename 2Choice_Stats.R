rm(list=ls())
library(ggplot2)
library(dplyr)
library(tidyr)
tmp<-read.csv("MaxIRSetup_Summary_Facet.csv")

tmp <- tmp %>%
  separate(col = Treatment,
           into = c("Sex", "Genotype"),
           sep = ",\\s*",
           remove = FALSE)

str(tmp)
tmp$Genotype<-factor(tmp$Genotype)
tmp$Sex<-factor(tmp$Sex)
tmp$TrackingRegion<-factor(tmp$TrackingRegion)
str(tmp)


######Stats#######

#check normality (experiments are almost always non-normal so can skip)

#anova <- (aov(tmp$PercentOn ~ tmp$Genotype*tmp$StartMin))
#summary(anova)
#plot(anova)


  
############# STATS FOR FIRST TEN MIN #########################
#separate out time frame to analyze
tmp_baseline <- dplyr::filter(tmp, FacetRange == "(0, 10)")
  
#run t-test on variable of interest (here PI) and save t-value
t.test_experimental <- t.test(FinalPI ~ Genotype, data = tmp_baseline)
  
library(broom)
library(tidyverse)
tidy_t.test_Experimental <- as.data.frame(tidy(t.test_experimental))
t.value_experimental <- tidy_t.test_Experimental$statistic
  
#create vector of all PI values (/variable of choice)
NW <- as.vector(tmp_baseline$FinalPI)
  
#create vector to store simulated t values
t.value_simulated <- c()


for (i in 1:100000) {
    NW.tmp <- sample(NW, size = length(NW), replace = FALSE)
    N.new <- NW.tmp[1:(length(NW)/2)]
    W.new <- NW.tmp[((length(NW)/2)+1):length(NW)]
    t.test_simulated <- t.test(x = N.new, y= W.new)
    tidy_t.test_simulated <- as.data.frame(tidy(t.test_simulated))
    t.value_new <- tidy_t.test_simulated$statistic
    t.value_simulated <- append(t.value_simulated, t.value_new)
    
  #print(t.value_simulated)
}  
  
p.value_baseline <- (sum(t.value_simulated >= t.value_experimental))/100000


############# STATS FOR MIDDLE 60 MIN #########################
#separate out time frame to analyze
tmp_preference <- dplyr::filter(tmp, FacetRange == "(10, 70)")

#run t-test on variable of interest (here PI) and save t-value
t.test_experimental <- t.test(FinalPI ~ Genotype, data = tmp_preference)

library(broom)
library(tidyverse)
tidy_t.test_Experimental <- as.data.frame(tidy(t.test_experimental))
t.value_experimental <- tidy_t.test_Experimental$statistic

#create vector of all PI values (/variable of choice)
NW <- as.vector(tmp_preference$FinalPI)

#create vector to store simulated t values
t.value_simulated <- c()


for (i in 1:100000) {
  NW.tmp <- sample(NW, size = length(NW), replace = FALSE)
  N.new <- NW.tmp[1:(length(NW)/2)]
  W.new <- NW.tmp[((length(NW)/2)+1):length(NW)]
  t.test_simulated <- t.test(x = N.new, y= W.new)
  tidy_t.test_simulated <- as.data.frame(tidy(t.test_simulated))
  t.value_new <- tidy_t.test_simulated$statistic
  t.value_simulated <- append(t.value_simulated, t.value_new)
  
  #print(t.value_simulated)
}  

p.value_preference <- (sum(t.value_simulated >= t.value_experimental))/100000
 



############# STATS FOR LAST 10 MIN #########################
#separate out time frame to analyze
tmp_persistence <- dplyr::filter(tmp, FacetRange == "(70, inf)")

#run t-test on variable of interest (here PI) and save t-value
t.test_experimental <- t.test(FinalPI ~ Genotype, data = tmp_persistence)

library(broom)
library(tidyverse)
tidy_t.test_Experimental <- as.data.frame(tidy(t.test_experimental))
t.value_experimental <- tidy_t.test_Experimental$statistic

#create vector of all PI values (/variable of choice)
NW <- as.vector(tmp_persistence$FinalPI)

#create vector to store simulated t values
t.value_simulated <- c()


for (i in 1:100000) {
  NW.tmp <- sample(NW, size = length(NW), replace = FALSE)
  N.new <- NW.tmp[1:(length(NW)/2)]
  W.new <- NW.tmp[((length(NW)/2)+1):length(NW)]
  t.test_simulated <- t.test(x = N.new, y= W.new)
  tidy_t.test_simulated <- as.data.frame(tidy(t.test_simulated))
  t.value_new <- tidy_t.test_simulated$statistic
  t.value_simulated <- append(t.value_simulated, t.value_new)
  
  #print(t.value_simulated)
}  

p.value_persistence <- (sum(t.value_simulated >= t.value_experimental))/100000



###OUTPUT P-VALUES#######
p.values.labels <- c("p.value_baseline", "p.value_preference", "p.value_persistence")
p.values <- c(p.value_baseline, p.value_preference, p.value_persistence)

labelled.p.values <- as.data.frame(cbind(p.values.labels, p.values))

dirname = dirname = "./"
outputfile<- paste("./",dirname,"/labelled_p_values_forExp2.csv",sep="")
write.csv(labelled.p.values,file=outputfile,row.names=FALSE)
