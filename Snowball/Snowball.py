#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = "David S. Batista"
__email__ = "dsbatista@inesc-id.pt"

import cPickle
import sys
import os
import codecs
import operator

from nltk import PunktWordTokenizer
from collections import defaultdict
from gensim.matutils import cossim
from Sentence import Sentence
from Pattern import Pattern
from Config import Config
from Tuple import Tuple


class Snowball(object):

    def __init__(self, config_file, seeds_file, sentences_file):
        self.patterns = list()
        self.processed_tuples = list()
        self.candidate_tuples = defaultdict(list)
        self.config = Config(config_file, seeds_file, sentences_file)

    def generate_tuples(self, sentences_file):
        """
        Generate tuples instances from a text file with sentences
        where named entities are already tagged
        """
        try:
            os.path.isfile("processed_tuples.pkl")
            f = open("processed_tuples.pkl", "r")
            print "\nLoading processed tuples from disk..."
            self.processed_tuples = cPickle.load(f)
            f.close()
            print len(self.processed_tuples), "tuples loaded"

        except IOError:
            print "\nGenerating relationship instances from sentences"
            f_sentences = codecs.open(sentences_file, encoding='utf-8')
            count = 0
            for line in f_sentences:
                count += 1
                if count % 10000 == 0:
                    sys.stdout.write(".")
                sentence = Sentence(line.strip(), self.config.e1_type, self.config.e2_type, self.config.max_tokens_away, self.config.min_tokens_away, self.config.context_window_size)
                for rel in sentence.relationships:
                    if rel.arg1type == self.config.e1_type and rel.arg2type == self.config.e2_type:
                        bef_tokens = self.tokenize(self, rel.before)
                        bet_tokens = self.tokenize(self, rel.between)
                        aft_tokens = self.tokenize(self, rel.after)
                        if not (bef_tokens == 0 and bet_tokens == 0 and aft_tokens == 0):
                            t = Tuple(rel.ent1, rel.ent2, rel.sentence, rel.before, rel.between, rel.after, self.config)
                        self.processed_tuples.append(t)
            f_sentences.close()

            print "\n", len(self.processed_tuples), "tuples generated"

            f = open("processed_tuples.pkl", "wb")
            cPickle.dump(self.processed_tuples, f)
            f.close()

    def start(self, tuples):
        """
        starts a bootstrap iteration
        """
        if tuples is not None:
            f = open(tuples, "r")
            print "\nLoading processed tuples from disk..."
            self.processed_tuples = cPickle.load(f)
            f.close()
            print len(self.processed_tuples), "tuples loaded"
        i = 0
        while i <= self.config.number_iterations:
            print "\n============================================="
            print "\nStarting iteration", i
            print "\nLooking for seed matches of:"
            for s in self.config.seed_tuples:
                print s.e1, '\t', s.e2

            # Looks for sentences macthing the seed instances
            count_matches, matched_tuples = self.match_seeds_tuples(self)

            if len(matched_tuples) == 0:
                print "\nNo seed matches found"
                sys.exit(0)

            else:
                print "\nNumber of seed matches found"
                sorted_counts = sorted(count_matches.items(), key=operator.itemgetter(1), reverse=True)
                for t in sorted_counts:
                    print t[0][0], '\t', t[0][1], t[1]

                # Cluster the matched instances: generate patterns/update patterns
                print "\nClustering matched instances to generate patterns"
                self.cluster_tuples(self, matched_tuples)

                # Eliminate patterns supported by less than 'min_pattern_support' tuples
                new_patterns = [p for p in self.patterns if len(p.tuples) >= 2]
                self.patterns = new_patterns
                print "\n", len(self.patterns), "patterns generated"
                if i == 0 and len(self.patterns) == 0:
                    print "No patterns generated"
                    sys.exit(0)

                # Look for sentences with occurrence of seeds semantic types (e.g., ORG - LOC)
                # This was already collect and its stored in: self.processed_tuples
                #
                # Measure the similarity of each occurrence with each extraction pattern
                # and store each pattern that has a similarity higher than a given threshold
                #
                # Each candidate tuple will then have a number of patterns that helped generate it,
                # each with an associated de gree of match. Snowball uses this infor
                print "\nCollecting instances based on extraction patterns"
                for t in self.processed_tuples:
                    sim_best = 0
                    for extraction_pattern in self.patterns:
                        score = self.similarity(self, t, extraction_pattern)
                        if score > sim_best:
                            sim_best = score
                            pattern_best = extraction_pattern

                    if sim_best >= self.config.threshold_similarity:
                        # if this instance was already extracted, check if it was by this extraction pattern
                        # if it was not extracted by this pattern, associated this pattern with it along with the
                        # similarity score
                        extraction_pattern.update_selectivity(t, self.config)
                        patterns = self.candidate_tuples[t]
                        if patterns is not None:
                            if pattern_best not in [x[0] for x in patterns]:
                                self.candidate_tuples[t].append((pattern_best, sim_best))
                                """
                                if i > 0:
                                    print t.e1, '\t', t.e2, '\t', t.sentence
                                    for x in self.candidate_tuples[t]:
                                        print x[0], x[1]
                                    print "\n"
                                """

                        # if this instance was not extracted before, associate theisextraciton pattern with the instance
                        # and the similarity score
                        else:
                            self.candidate_tuples[t].append((pattern_best, sim_best))

                    # update extraction pattern confidence
                    extraction_pattern.confidence_old = extraction_pattern.confidence
                    extraction_pattern.update_confidence()

                # update tuple confidence based on patterns confidence
                print "\nCalculating tuples confidence"
                for t in self.candidate_tuples.keys():
                    confidence = 1
                    t.confidence_old = t.confidence
                    for p in self.candidate_tuples.get(t):
                        confidence *= 1 - (p[0].confidence * p[1])
                    t.confidence = 1 - confidence

                    # use past confidence values to calculate new confidence
                    # if parameter Wupdt < 0.5 the system trusts new examples less on each iteration
                    # which will lead to more conservative patterns and have a damping effect.
                    if i > 0:
                        t.confidence = t.confidence * self.config.wUpdt + t.confidence_old * (1 - self.config.wUpdt)

                # update seed set of tuples to use in next iteration
                # seeds = { T | Conf(T) > min_tuple_confidence }
                if i+1 < self.config.number_iterations:
                    print "Adding tuples to seed with confidence =>" + str(self.config.instance_confidance)
                    for t in self.candidate_tuples.keys():
                        if t.confidence >= self.config.instance_confidance:
                            self.config.seed_tuples.add(t)
                            print t.e1, '\t', t.e2

                # increment the number of iterations
                i += 1

        print "\nWriting extracted relationships to disk"
        f_output = open("relationships.txt", "w")
        tmp = sorted(self.candidate_tuples, key=lambda tuple: tuple.confidence, reverse=True)
        for t in tmp:
            f_output.write("instance: "+t.e1.encode("utf8")+'\t'+t.e2.encode("utf8")+'\tscore:'+str(t.confidence)+'\n')
            f_output.write("sentence: "+t.sentence.encode("utf8")+'\n')
            # writer patterns that extracted this tuple
            patterns = set()
            for pattern in self.candidate_tuples[t]:
                patterns.add(pattern[0])
            for p in patterns:
                p.merge_tuple_patterns()
                f_output.write("pattern: " + ', '.join(p.tuple_patterns) + '\n')
            f_output.write("\n")
        f_output.close()

        print "Writing generated patterns to disk"
        f_output = open("patterns.txt", "w")
        tmp = sorted(self.patterns, reverse=True)
        for p in tmp:
            f_output.write(str([str(t) for t in p.tuples])+'\t'+str(p.confidence)+'\n')
        f_output.close()

    @staticmethod
    def similarity(self, t, extraction_pattern):
        (bef, bet, aft) = (0, 0, 0)

        if t.bef_vector is not None:
            bef = cossim(t.bef_vector, extraction_pattern.centroid_bef)

        if t.bet_vector is not None:
            bet = cossim(t.bet_vector, extraction_pattern.centroid_bet)

        if t.aft_vector is not None:
            aft = cossim(t.aft_vector, extraction_pattern.centroid_aft)

        return self.config.alpha*bef + self.config.beta*bet + self.config.gamma*aft

    @staticmethod
    def cluster_tuples(self, matched_tuples):
        """
        single-pass clustering
        """
        start = 0
        # Initialize: if no patterns exist, first tuple goes to first cluster
        if len(self.patterns) == 0:
            c1 = Pattern(matched_tuples[0])
            self.patterns.append(c1)
            start = 1

        # Compute the similarity between an instance with each pattern
        # go through all tuples
        for i in range(start, len(matched_tuples), 1):
            t = matched_tuples[i]
            max_similarity = 0
            max_similarity_cluster_index = 0

            # go through all patterns(clusters of tuples) and find the one with the
            # highest similarity score

            # TODO: vou estar a acrescentar novos patterns, à medida que faço as iterações, verificar que "
            # TODO: novos patterns adicionados são tidos em consideração"

            for w in range(0, len(self.patterns), 1):
                extraction_pattern = self.patterns[w]
                score = self.similarity(self, t, extraction_pattern)
                """
                print "\ntuple     :"
                print t
                print t.e1, '\t', t.e2
                print t.sentence
                print "\nextraction:\n", extraction_pattern
                print "score     :", score
                print "\n"
                """
                if score > max_similarity:
                    max_similarity = score
                    max_similarity_cluster_index = w

            # if max_similarity < min_degree_match create a new cluster having this tuple as the centroid
            if max_similarity < self.config.threshold_similarity:
                c = Pattern(t)
                self.patterns.append(c)

            # if max_similarity >= min_degree_match add to the cluster with the highest similarity
            else:
                self.patterns[max_similarity_cluster_index].add_tuple(t)

    @staticmethod
    def tokenize(self, text):
        return [word for word in PunktWordTokenizer().tokenize(text.lower()) if word not in self.config.stopwords]

    @staticmethod
    def match_seeds_tuples(self):
        """
        checks if an extracted tuple matches seeds tuples
        """
        matched_tuples = list()
        count_matches = dict()
        for t in self.processed_tuples:
            for s in self.config.seed_tuples:
                if t.e1 == s.e1 and t.e2 == s.e2:
                    matched_tuples.append(t)
                    try:
                        count_matches[(t.e1, t.e2)] += 1
                    except KeyError:
                        count_matches[(t.e1, t.e2)] = 1

        return count_matches, matched_tuples


def main():
    configuration = sys.argv[1]
    sentences_file = sys.argv[2]
    seeds_file = sys.argv[3]
    snowball = Snowball(configuration, seeds_file, sentences_file)
    if sentences_file.endswith('.pkl'):
        print "Loading pre-processed sentences", sentences_file
        snowball.start(tuples=sentences_file)
    else:
        snowball.generate_tuples(sentences_file)
        snowball.start(tuples=None)


if __name__ == "__main__":
    main()