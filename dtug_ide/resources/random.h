#ifndef DATA_GENERATOR_RANDOM_H
#define DATA_GENERATOR_RANDOM_H

/*
 * Data-Generator - modern C++11 single-header test-data generator.
 *
 * Quick start:
 *
 *   #include "random.h"
 *
 *   int main() {
 *       pg::ProblemOptions options;
 *       options.case_count = 10;
 *       options.output_directory = "data";
 *       pg::Problem problem(options);
 *       problem.generate([](pg::Case& out, std::int64_t) {
 *           const int n = out.random.integer(1, 100);
 *           out.line(n);
 *           out.line(out.random.integers(n, -1000, 1000));
 *       });
 *   }
 *
 * The repository's Python package provides the matching declarative API,
 * multi-language solution runner, JSON protocol and PyQt6 designer.
 */

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unordered_set>
#include <utility>
#include <vector>

#include <sys/stat.h>
#include <sys/types.h>
#ifdef _WIN32
#include <direct.h>
#endif

#if __cplusplus >= 201703L
#define PG_NODISCARD [[nodiscard]]
#else
#define PG_NODISCARD
#endif

namespace pg {

using i64 = std::int64_t;
using u64 = std::uint64_t;

inline u64 splitmix64(u64 value) noexcept {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

inline u64 random_seed() {
    const auto clock = static_cast<u64>(
        std::chrono::high_resolution_clock::now().time_since_epoch().count());
    std::random_device device;
    const u64 entropy = (static_cast<u64>(device()) << 32U) ^ device();
    return splitmix64(clock ^ entropy);
}

inline u64 add_mod(u64 a, u64 b, u64 mod) noexcept {
    return a >= mod - b ? a - (mod - b) : a + b;
}

inline u64 multiply_mod(u64 a, u64 b, u64 mod) noexcept {
    u64 result = 0;
    a %= mod;
    while (b != 0) {
        if ((b & 1U) != 0U) result = add_mod(result, a, mod);
        b >>= 1U;
        if (b != 0) a = add_mod(a, a, mod);
    }
    return result;
}

inline u64 power_mod(u64 base, u64 exponent, u64 mod) noexcept {
    u64 result = 1;
    while (exponent != 0) {
        if ((exponent & 1U) != 0U) result = multiply_mod(result, base, mod);
        exponent >>= 1U;
        if (exponent != 0) base = multiply_mod(base, base, mod);
    }
    return result;
}

inline bool is_prime(u64 value) noexcept {
    if (value < 2) return false;
    for (const u64 prime : {2ULL, 3ULL, 5ULL, 7ULL, 11ULL, 13ULL, 17ULL,
                            19ULL, 23ULL, 29ULL, 31ULL, 37ULL}) {
        if (value % prime == 0) return value == prime;
    }
    u64 odd = value - 1;
    unsigned shifts = 0;
    while ((odd & 1U) == 0U) {
        odd >>= 1U;
        ++shifts;
    }
    // This base set is deterministic for the complete uint64 range.
    for (const u64 base : {2ULL, 325ULL, 9375ULL, 28178ULL, 450775ULL,
                           9780504ULL, 1795265022ULL}) {
        if (base % value == 0) continue;
        u64 current = power_mod(base % value, odd, value);
        if (current == 1 || current == value - 1) continue;
        bool composite = true;
        for (unsigned round = 1; round < shifts; ++round) {
            current = multiply_mod(current, current, value);
            if (current == value - 1) {
                composite = false;
                break;
            }
        }
        if (composite) return false;
    }
    return true;
}

struct Edge {
    i64 from;
    i64 to;
    bool has_weight;
    i64 weight;

    Edge(i64 from_value = 0, i64 to_value = 0)
        : from(from_value), to(to_value), has_weight(false), weight(0) {}

    Edge(i64 from_value, i64 to_value, i64 weight_value)
        : from(from_value), to(to_value), has_weight(true), weight(weight_value) {}
};

inline std::ostream& operator<<(std::ostream& stream, const Edge& edge) {
    stream << edge.from << ' ' << edge.to;
    if (edge.has_weight) stream << ' ' << edge.weight;
    return stream;
}

struct Graph {
    std::size_t vertex_count;
    bool directed;
    std::vector<Edge> edges;

    Graph(std::size_t vertices = 0, bool is_directed = false)
        : vertex_count(vertices), directed(is_directed) {}
};

inline std::ostream& operator<<(std::ostream& stream, const Graph& graph) {
    for (std::size_t index = 0; index < graph.edges.size(); ++index) {
        if (index != 0) stream << '\n';
        stream << graph.edges[index];
    }
    return stream;
}

enum class TreeShape { random, chain, star, binary };

enum class Distribution { uniform, minimum, maximum, boundary, small, large, mixed };
enum class IntegerConstraint { any, odd, even, prime };

struct TreeOptions {
    i64 first_vertex = 1;
    TreeShape shape = TreeShape::random;
    bool directed = false;
    bool weighted = false;
    i64 minimum_weight = 1;
    i64 maximum_weight = 100;
};

struct GraphOptions {
    i64 first_vertex = 1;
    bool directed = false;
    bool connected = true;
    bool acyclic = false;
    bool weighted = false;
    i64 minimum_weight = 1;
    i64 maximum_weight = 100;
};

class Random {
public:
    explicit Random(u64 seed = random_seed()) : seed_(seed), engine_(seed) {}

    void reseed(u64 value) {
        seed_ = value;
        engine_.seed(value);
    }

    PG_NODISCARD u64 seed() const noexcept { return seed_; }
    PG_NODISCARD std::mt19937_64& engine() noexcept { return engine_; }

    template <class Integer>
    PG_NODISCARD Integer integer(Integer minimum, Integer maximum) {
        static_assert(std::is_integral<Integer>::value &&
                          !std::is_same<Integer, bool>::value,
                      "integer() requires a non-bool integral type");
        if (minimum > maximum) {
            throw std::invalid_argument("minimum must not exceed maximum");
        }
        return std::uniform_int_distribution<Integer>(minimum, maximum)(engine_);
    }

    template <class Integer = int>
    PG_NODISCARD Integer integer(Integer maximum) {
        return integer<Integer>(Integer{0}, maximum);
    }

    template <class Real = double>
    PG_NODISCARD Real real(Real minimum = Real{0}, Real maximum = Real{1}) {
        static_assert(std::is_floating_point<Real>::value,
                      "real() requires a floating-point type");
        if (!(minimum <= maximum)) {
            throw std::invalid_argument("minimum must not exceed maximum");
        }
        if (minimum == maximum) return minimum;
        return std::uniform_real_distribution<Real>(minimum, maximum)(engine_);
    }

    PG_NODISCARD bool chance(double probability = 0.5) {
        if (probability < 0.0 || probability > 1.0) {
            throw std::invalid_argument("probability must be in [0, 1]");
        }
        return std::bernoulli_distribution(probability)(engine_);
    }

    template <class Value>
    PG_NODISCARD const Value& weighted_choice(
        const std::vector<Value>& values,
        const std::vector<double>& weights) {
        if (values.empty() || values.size() != weights.size()) {
            throw std::invalid_argument("weighted_choice needs equally sized non-empty arrays");
        }
        double total = 0.0;
        for (const double weight : weights) {
            if (weight < 0.0) throw std::invalid_argument("weights must be non-negative");
            total += weight;
        }
        if (!(total > 0.0)) throw std::invalid_argument("at least one weight must be positive");
        const double selected = real(0.0, total);
        double prefix = 0.0;
        for (std::size_t index = 0; index < weights.size(); ++index) {
            prefix += weights[index];
            if (selected <= prefix) return values[index];
        }
        return values.back();
    }

    template <class Sequence>
    PG_NODISCARD const typename Sequence::value_type& choice(const Sequence& values) {
        if (values.empty()) throw std::invalid_argument("cannot choose from an empty sequence");
        return values[integer<std::size_t>(0, values.size() - 1)];
    }

    template <class Iterator>
    void shuffle(Iterator first, Iterator last) {
        std::shuffle(first, last, engine_);
    }

    template <class Sequence>
    void shuffle(Sequence& values) {
        shuffle(values.begin(), values.end());
    }

    PG_NODISCARD i64 odd(i64 minimum, i64 maximum) {
        return parity(minimum, maximum, 1);
    }

    PG_NODISCARD i64 even(i64 minimum, i64 maximum) {
        return parity(minimum, maximum, 0);
    }

    PG_NODISCARD i64 prime(i64 minimum, i64 maximum) {
        return prime(minimum, maximum, Distribution::uniform, 0.5);
    }

    PG_NODISCARD i64 constrained_integer(
        i64 minimum, i64 maximum,
        IntegerConstraint constraint = IntegerConstraint::any,
        Distribution distribution = Distribution::uniform,
        double phase = 0.5) {
        if (minimum > maximum) {
            throw std::invalid_argument("minimum must not exceed maximum");
        }
        if (constraint == IntegerConstraint::prime) {
            return prime(minimum, maximum, distribution, phase);
        }
        if (constraint == IntegerConstraint::odd ||
            constraint == IntegerConstraint::even) {
            const int wanted = constraint == IntegerConstraint::odd ? 1 : 0;
            if (((minimum % 2) + 2) % 2 != wanted) {
                if (minimum == std::numeric_limits<i64>::max()) {
                    throw std::invalid_argument("range contains no matching value");
                }
                ++minimum;
            }
            if (((maximum % 2) + 2) % 2 != wanted) --maximum;
            if (minimum > maximum) {
                throw std::invalid_argument("range contains no matching value");
            }
            const u64 slots = (static_cast<u64>(maximum) -
                               static_cast<u64>(minimum)) / 2ULL;
            return add_offset(minimum, biased_slot(slots, distribution, phase) * 2ULL);
        }
        const u64 span = static_cast<u64>(maximum) - static_cast<u64>(minimum);
        return add_offset(minimum, biased_slot(span, distribution, phase));
    }

    PG_NODISCARD i64 prime(i64 minimum, i64 maximum,
                            Distribution distribution, double phase = 0.5) {
        if (minimum > maximum || maximum < 2) {
            throw std::invalid_argument("range contains no prime");
        }
        minimum = std::max<i64>(minimum, 2);
        if (maximum == 2) return 2;
        const i64 odd_minimum = std::max<i64>(minimum, 3);
        for (int attempt = 0; attempt < 256; ++attempt) {
            i64 candidate = constrained_integer(odd_minimum, maximum,
                                                IntegerConstraint::odd,
                                                distribution, phase);
            if (is_prime(static_cast<u64>(candidate))) return candidate;
        }
        const u64 span = static_cast<u64>(maximum - minimum);
        if (span > 2000000ULL) {
            throw std::runtime_error("no prime found quickly; use a narrower range");
        }
        std::vector<i64> candidates;
        for (i64 value = minimum; value <= maximum; ++value) {
            if (is_prime(static_cast<u64>(value))) candidates.push_back(value);
            if (value == std::numeric_limits<i64>::max()) break;
        }
        if (candidates.empty()) throw std::invalid_argument("range contains no prime");
        return candidates[biased_slot(candidates.size() - 1, distribution, phase)];
    }

    PG_NODISCARD std::string string(
        std::size_t length,
        const std::string& alphabet = "abcdefghijklmnopqrstuvwxyz") {
        if (alphabet.empty()) throw std::invalid_argument("alphabet must not be empty");
        std::string result;
        result.reserve(length);
        for (std::size_t index = 0; index < length; ++index) {
            result.push_back(alphabet[integer<std::size_t>(0, alphabet.size() - 1)]);
        }
        return result;
    }

    PG_NODISCARD std::vector<i64> integers(
        std::size_t count, i64 minimum, i64 maximum, bool distinct = false,
        bool sorted = false) {
        if (minimum > maximum) {
            throw std::invalid_argument("minimum must not exceed maximum");
        }
        std::vector<i64> result;
        result.reserve(count);
        if (!distinct) {
            for (std::size_t index = 0; index < count; ++index) {
                result.push_back(integer<i64>(minimum, maximum));
            }
        } else {
            const long double span = static_cast<long double>(maximum) -
                                     static_cast<long double>(minimum) + 1.0L;
            if (static_cast<long double>(count) > span) {
                throw std::invalid_argument("distinct count exceeds range size");
            }
            if (span <= 2000000.0L &&
                count * 3 > static_cast<std::size_t>(span)) {
                result.resize(static_cast<std::size_t>(span));
                std::iota(result.begin(), result.end(), minimum);
                shuffle(result);
                result.resize(count);
            } else {
                std::unordered_set<i64> selected;
                while (selected.size() < count) {
                    selected.insert(integer<i64>(minimum, maximum));
                }
                result.assign(selected.begin(), selected.end());
                shuffle(result);
            }
        }
        if (sorted) std::sort(result.begin(), result.end());
        return result;
    }

    PG_NODISCARD std::vector<i64> smart_integers(
        std::size_t count, i64 minimum, i64 maximum,
        IntegerConstraint constraint = IntegerConstraint::any,
        Distribution distribution = Distribution::mixed,
        double phase = 0.5, bool distinct = false, bool sorted = false) {
        std::vector<i64> result;
        result.reserve(count);
        std::unordered_set<i64> used;
        const std::size_t attempts_limit = std::max<std::size_t>(1000, count * 100);
        for (std::size_t attempt = 0;
             result.size() < count && attempt < attempts_limit; ++attempt) {
            const i64 value = constrained_integer(minimum, maximum, constraint,
                                                  distribution, phase);
            if (!distinct || used.insert(value).second) result.push_back(value);
        }
        if (result.size() != count) {
            throw std::runtime_error("could not generate enough distinct constrained integers");
        }
        if (sorted) std::sort(result.begin(), result.end());
        return result;
    }

    PG_NODISCARD std::vector<i64> sample(
        std::size_t count, i64 minimum, i64 maximum, bool sorted = false) {
        return integers(count, minimum, maximum, true, sorted);
    }

    PG_NODISCARD std::vector<std::vector<i64>> matrix(
        std::size_t rows, std::size_t columns, i64 minimum, i64 maximum) {
        std::vector<std::vector<i64>> result;
        result.reserve(rows);
        for (std::size_t row = 0; row < rows; ++row) {
            result.push_back(integers(columns, minimum, maximum));
        }
        return result;
    }

    PG_NODISCARD std::vector<std::string> grid(
        std::size_t rows, std::size_t columns,
        const std::string& alphabet = ".#") {
        if (alphabet.empty()) throw std::invalid_argument("alphabet must not be empty");
        std::vector<std::string> result;
        result.reserve(rows);
        for (std::size_t row = 0; row < rows; ++row) {
            result.push_back(string(columns, alphabet));
        }
        return result;
    }

    PG_NODISCARD std::string bracket_sequence(
        std::size_t pairs, const std::string& bracket_types = "()") {
        if (bracket_types.empty() || bracket_types.size() % 2 != 0) {
            throw std::invalid_argument("bracket_types must contain open/close pairs");
        }
        std::string result;
        result.reserve(pairs * 2);
        std::vector<char> closing;
        std::size_t opened = 0;
        while (result.size() < pairs * 2) {
            const bool must_open = closing.empty();
            const bool may_open = opened < pairs;
            if (must_open || (may_open && chance(0.58))) {
                const std::size_t kind = integer<std::size_t>(0, bracket_types.size() / 2 - 1);
                result.push_back(bracket_types[kind * 2]);
                closing.push_back(bracket_types[kind * 2 + 1]);
                ++opened;
            } else {
                result.push_back(closing.back());
                closing.pop_back();
            }
        }
        return result;
    }

    PG_NODISCARD std::vector<i64> permutation(std::size_t count, i64 first = 1) {
        if (count > static_cast<std::size_t>(std::numeric_limits<i64>::max()) ||
            first > std::numeric_limits<i64>::max() - static_cast<i64>(count)) {
            throw std::overflow_error("permutation range overflows int64");
        }
        std::vector<i64> result(count);
        std::iota(result.begin(), result.end(), first);
        shuffle(result);
        return result;
    }

    PG_NODISCARD std::vector<i64> partition(
        i64 total, std::size_t parts, i64 minimum_part = 0) {
        if (parts == 0) {
            if (total == 0) return {};
            throw std::invalid_argument("non-zero total needs at least one part");
        }
        if (minimum_part < 0 ||
            minimum_part > total / static_cast<i64>(parts)) {
            throw std::invalid_argument("minimum parts exceed total");
        }
        total -= minimum_part * static_cast<i64>(parts);
        if (parts == 1) return {total + minimum_part};
        if (static_cast<u64>(total) >
            static_cast<u64>(std::numeric_limits<i64>::max()) - parts) {
            throw std::overflow_error("partition range overflows int64");
        }
        auto bars = integers(parts - 1, 1,
                             total + static_cast<i64>(parts) - 1,
                             true, true);
        std::vector<i64> result;
        result.reserve(parts);
        i64 previous = 0;
        for (const i64 bar : bars) {
            result.push_back(bar - previous - 1 + minimum_part);
            previous = bar;
        }
        result.push_back(total + static_cast<i64>(parts) - previous - 1 +
                         minimum_part);
        return result;
    }

    PG_NODISCARD Graph tree(std::size_t vertices,
                            TreeOptions options = TreeOptions()) {
        if (vertices == 0) throw std::invalid_argument("tree needs at least one vertex");
        validate_weights(options.weighted, options.minimum_weight,
                         options.maximum_weight);
        const auto labels = permutation(vertices, options.first_vertex);
        Graph result(vertices, options.directed);
        result.edges.reserve(vertices - 1);
        for (std::size_t index = 1; index < vertices; ++index) {
            std::size_t parent = 0;
            switch (options.shape) {
                case TreeShape::random:
                    parent = integer<std::size_t>(0, index - 1);
                    break;
                case TreeShape::chain:
                    parent = index - 1;
                    break;
                case TreeShape::star:
                    parent = 0;
                    break;
                case TreeShape::binary:
                    parent = (index - 1) / 2;
                    break;
            }
            Edge edge(labels[parent], labels[index]);
            if (options.weighted) {
                edge.has_weight = true;
                edge.weight = integer<i64>(options.minimum_weight,
                                           options.maximum_weight);
            }
            if (!options.directed && chance()) std::swap(edge.from, edge.to);
            result.edges.push_back(edge);
        }
        shuffle(result.edges);
        return result;
    }

    PG_NODISCARD Graph graph(std::size_t vertices, std::size_t edge_count,
                             GraphOptions options = GraphOptions()) {
        if (vertices == 0) throw std::invalid_argument("graph needs at least one vertex");
        if (options.acyclic && !options.directed) {
            throw std::invalid_argument("acyclic graph generation requires directed=true");
        }
        validate_weights(options.weighted, options.minimum_weight,
                         options.maximum_weight);
        const long double maximum_edges = options.acyclic
            ? static_cast<long double>(vertices) * (vertices - 1) / 2.0L
            : options.directed
                ? static_cast<long double>(vertices) * (vertices - 1)
                : static_cast<long double>(vertices) * (vertices - 1) / 2.0L;
        if (static_cast<long double>(edge_count) > maximum_edges) {
            throw std::invalid_argument("too many edges for the requested simple graph");
        }
        if (options.connected && edge_count + 1 < vertices) {
            throw std::invalid_argument("a connected graph needs at least n-1 edges");
        }

        auto labels = permutation(vertices, options.first_vertex);
        Graph result(vertices, options.directed);
        result.edges.reserve(edge_count);
        std::set<std::pair<i64, i64>> used;
        auto key_for = [&](i64 from, i64 to) {
            if (options.directed) return std::make_pair(from, to);
            return std::make_pair(std::min(from, to), std::max(from, to));
        };
        auto add = [&](i64 from, i64 to) {
            if (from == to || !used.insert(key_for(from, to)).second) return false;
            Edge edge(from, to);
            if (options.weighted) {
                edge.has_weight = true;
                edge.weight = integer<i64>(options.minimum_weight,
                                           options.maximum_weight);
            }
            result.edges.push_back(edge);
            return true;
        };

        if (options.connected && vertices > 1) {
            for (std::size_t index = 1; index < vertices; ++index) {
                const std::size_t parent = options.acyclic
                    ? integer<std::size_t>(0, index - 1)
                    : integer<std::size_t>(0, index - 1);
                i64 from = labels[parent];
                i64 to = labels[index];
                if (options.directed && !options.acyclic && chance()) {
                    std::swap(from, to);
                }
                add(from, to);
            }
        }

        const std::size_t remaining = edge_count - result.edges.size();
        if (remaining > static_cast<std::size_t>(maximum_edges / 3.0L)) {
            std::vector<std::pair<i64, i64>> candidates;
            candidates.reserve(static_cast<std::size_t>(maximum_edges));
            for (std::size_t left = 0; left < vertices; ++left) {
                for (std::size_t right = 0; right < vertices; ++right) {
                    if (left == right) continue;
                    if (!options.directed && right < left) continue;
                    if (options.acyclic && right < left) continue;
                    const auto edge = std::make_pair(labels[left], labels[right]);
                    if (used.count(key_for(edge.first, edge.second)) == 0) {
                        candidates.push_back(edge);
                    }
                }
            }
            shuffle(candidates);
            for (std::size_t index = 0; result.edges.size() < edge_count; ++index) {
                add(candidates[index].first, candidates[index].second);
            }
        } else {
            while (result.edges.size() < edge_count) {
                std::size_t left = integer<std::size_t>(0, vertices - 1);
                std::size_t right = integer<std::size_t>(0, vertices - 1);
                if (options.acyclic && left > right) std::swap(left, right);
                add(labels[left], labels[right]);
            }
        }
        shuffle(result.edges);
        return result;
    }

private:
    PG_NODISCARD static i64 add_offset(i64 minimum, u64 offset) {
        if (minimum >= 0) return minimum + static_cast<i64>(offset);
        const u64 to_zero = static_cast<u64>(-(minimum + 1)) + 1ULL;
        if (offset < to_zero) return minimum + static_cast<i64>(offset);
        return static_cast<i64>(offset - to_zero);
    }

    PG_NODISCARD u64 biased_slot(u64 maximum, Distribution distribution,
                                  double phase) {
        if (maximum == 0) return 0;
        if (distribution == Distribution::minimum) return 0;
        if (distribution == Distribution::maximum) return maximum;
        if (distribution == Distribution::boundary) return chance() ? 0 : maximum;
        if (distribution == Distribution::small ||
            distribution == Distribution::large) {
            const long double draw = real<long double>(0.0L, 1.0L);
            const u64 offset = static_cast<u64>(draw * draw * maximum);
            return distribution == Distribution::small ? offset : maximum - offset;
        }
        if (distribution == Distribution::mixed) {
            if (phase <= 0.001) return 0;
            if (phase >= 0.999) return maximum;
            if (chance(0.18)) return chance() ? 0 : maximum;
        }
        return integer<u64>(0, maximum);
    }

    PG_NODISCARD i64 parity(i64 minimum, i64 maximum, int wanted) {
        if (minimum > maximum) {
            throw std::invalid_argument("minimum must not exceed maximum");
        }
        if (((minimum % 2) + 2) % 2 != wanted) {
            if (minimum == std::numeric_limits<i64>::max()) {
                throw std::invalid_argument("range contains no matching value");
            }
            ++minimum;
        }
        if (((maximum % 2) + 2) % 2 != wanted) --maximum;
        if (minimum > maximum) {
            throw std::invalid_argument("range contains no matching value");
        }
        const u64 slots = (static_cast<u64>(maximum) -
                           static_cast<u64>(minimum)) / 2ULL;
        return add_offset(minimum, integer<u64>(0, slots) * 2ULL);
    }

    static void validate_weights(bool weighted, i64 minimum, i64 maximum) {
        if (weighted && minimum > maximum) {
            throw std::invalid_argument("minimum weight must not exceed maximum weight");
        }
    }

    u64 seed_;
    std::mt19937_64 engine_;
};

class Case {
public:
    explicit Case(u64 seed = random_seed()) : random(seed) {}

    void line() { stream_ << '\n'; }

    template <class Value>
    void line(const Value& value) {
        stream_ << value << '\n';
    }

    template <class Value, class Allocator>
    void line(const std::vector<Value, Allocator>& values) {
        bool first = true;
        for (typename std::vector<Value, Allocator>::const_iterator iterator =
                 values.begin();
             iterator != values.end(); ++iterator) {
            if (!first) stream_ << ' ';
            stream_ << *iterator;
            first = false;
        }
        stream_ << '\n';
    }

    template <class First, class Second, class... Rest>
    void line(const First& first_value, const Second& second_value,
              const Rest&... rest) {
        bool first = true;
        write_values(first, first_value, second_value, rest...);
        stream_ << '\n';
    }

    void lines(const Graph& graph) {
        for (const auto& edge : graph.edges) line(edge);
    }

    template <class Value, class Allocator>
    void lines(const std::vector<Value, Allocator>& values) {
        for (typename std::vector<Value, Allocator>::const_iterator iterator =
                 values.begin(); iterator != values.end(); ++iterator) {
            line(*iterator);
        }
    }

    void text(const std::string& value) { stream_ << value; }
    void space() { stream_ << ' '; }
    void blank() { stream_ << '\n'; }
    PG_NODISCARD std::string str() const { return stream_.str(); }

    Random random;

private:
    void write_values(bool&) {}

    template <class First, class... Rest>
    void write_values(bool& is_first, const First& value, const Rest&... rest) {
        if (!is_first) stream_ << ' ';
        stream_ << value;
        is_first = false;
        write_values(is_first, rest...);
    }

    std::ostringstream stream_;
};

namespace detail {

inline bool is_separator(char value) { return value == '/' || value == '\\'; }

inline char path_separator() {
#ifdef _WIN32
    return '\\';
#else
    return '/';
#endif
}

inline std::string normalize_path(std::string path) {
    for (std::size_t index = 0; index < path.size(); ++index) {
        if (is_separator(path[index])) path[index] = path_separator();
    }
    return path;
}

inline bool path_exists(const std::string& path) {
#ifdef _WIN32
    struct _stat information;
    return _stat(path.c_str(), &information) == 0;
#else
    struct stat information;
    return stat(path.c_str(), &information) == 0;
#endif
}

inline bool path_is_directory(const std::string& path) {
#ifdef _WIN32
    struct _stat information;
    if (_stat(path.c_str(), &information) != 0) return false;
    return (information.st_mode & _S_IFDIR) != 0;
#else
    struct stat information;
    if (stat(path.c_str(), &information) != 0) return false;
    return S_ISDIR(information.st_mode);
#endif
}

inline int make_directory(const std::string& path) {
#ifdef _WIN32
    return _mkdir(path.c_str());
#else
    return mkdir(path.c_str(), 0755);
#endif
}

inline bool create_directories(const std::string& raw_path) {
    if (raw_path.empty()) return false;
    const std::string path = normalize_path(raw_path);
    if (path_exists(path)) return path_is_directory(path);
    std::size_t start = 0;
#ifdef _WIN32
    if (path.size() >= 3 && path[1] == ':' && is_separator(path[2])) start = 3;
#else
    if (!path.empty() && is_separator(path[0])) start = 1;
#endif
    for (std::size_t index = start; index <= path.size(); ++index) {
        if (index != path.size() && !is_separator(path[index])) continue;
        const std::string part = path.substr(0, index);
        if (part.empty() || part == ".") continue;
        if (!path_exists(part) && make_directory(part) != 0 && errno != EEXIST) {
            return false;
        }
        if (!path_is_directory(part)) return false;
    }
    return path_is_directory(path);
}

inline std::string join_path(const std::string& directory,
                             const std::string& name) {
    if (directory.empty() || directory == ".") return name;
    if (is_separator(directory[directory.size() - 1])) return directory + name;
    return directory + path_separator() + name;
}

inline bool replace_file(const std::string& source,
                         const std::string& destination) {
    std::remove(destination.c_str());
    return std::rename(source.c_str(), destination.c_str()) == 0;
}

}  // namespace detail

struct TestGroup {
    std::string name;
    std::size_t count = 0;
    std::string profile = "mixed";
    int subtask_id = -1;
    int score = -1;
    int time_limit_ms = -1;
    int memory_limit_kb = -1;
    bool pretest = false;
};

struct GenerationContext {
    i64 id = 0;
    std::size_t index = 0;
    std::size_t count = 0;
    double phase = 0.5;
    u64 seed = 0;
    std::string group;
    std::string profile = "mixed";
    std::size_t group_index = 0;
    std::size_t group_count = 0;
    double group_phase = 0.5;
};

struct ProblemOptions {
    std::string output_directory = "data";
    std::string file_prefix;
    std::size_t case_count = 10;
    i64 first_id = 1;
    u64 seed = random_seed();
    std::string solution_command;
    bool overwrite = true;
    std::size_t maximum_attempts = 100;
    std::vector<TestGroup> groups;
    bool emit_luogu_config = false;
};

struct GenerationResult {
    bool ok = true;
    std::size_t generated = 0;
    u64 seed = 0;
    std::vector<std::string> errors;

    explicit operator bool() const noexcept { return ok; }
};

inline std::string shell_quote(const std::string& text) {
#ifdef _WIN32
    std::string result = "\"";
    for (const char value : text) result += value == '"' ? "\"\"" : std::string(1, value);
    return result + "\"";
#else
    std::string result = "'";
    for (const char value : text) result += value == '\'' ? "'\\''" : std::string(1, value);
    return result + "'";
#endif
}

inline void replace_all(std::string& text, const std::string& needle,
                        const std::string& replacement) {
    std::size_t position = 0;
    while (!needle.empty() &&
           (position = text.find(needle, position)) != std::string::npos) {
        text.replace(position, needle.size(), replacement);
        position += replacement.size();
    }
}

class Problem {
public:
    using Generator = std::function<void(Case&, i64)>;
    using ContextGenerator = std::function<void(Case&, const GenerationContext&)>;
    using Validator = std::function<bool(const std::string&, i64)>;
    using Progress = std::function<void(std::size_t, std::size_t,
                                        const std::string&)>;

    explicit Problem(ProblemOptions options = ProblemOptions())
        : options_(std::move(options)) {}

    Problem& validate_with(Validator validator) {
        validator_ = std::move(validator);
        return *this;
    }

    Problem& on_progress(Progress progress) {
        progress_ = std::move(progress);
        return *this;
    }

    PG_NODISCARD GenerationResult generate(const Generator& generator) const {
        if (!generator) return missing_generator();
        return generate(ContextGenerator(
            [&](Case& data, const GenerationContext& context) {
                generator(data, context.id);
            }));
    }

    PG_NODISCARD GenerationResult generate(const ContextGenerator& generator) const {
        GenerationResult result;
        result.seed = options_.seed;
        if (!generator) {
            return missing_generator();
        }

        if (!detail::create_directories(options_.output_directory)) {
            result.ok = false;
            result.errors.push_back("cannot create output directory: " +
                                    options_.output_directory);
            return result;
        }

        const std::size_t total = total_case_count();
        for (std::size_t index = 0; index < total; ++index) {
            const GenerationContext base_context = context_for(index, total);
            const i64 id = base_context.id;
            const std::string stem = options_.file_prefix + std::to_string(id);
            const std::string input = detail::join_path(
                options_.output_directory, stem + ".in");
            const std::string output = detail::join_path(
                options_.output_directory, stem + ".out");
            if (!options_.overwrite &&
                (detail::path_exists(input) || detail::path_exists(output))) {
                result.ok = false;
                result.errors.push_back("case already exists: " + stem);
                continue;
            }

            try {
                std::string content;
                bool accepted = false;
                for (std::size_t attempt = 0; attempt < options_.maximum_attempts;
                     ++attempt) {
                    Case data(splitmix64(options_.seed ^ static_cast<u64>(id) ^
                                         splitmix64(attempt)));
                    GenerationContext context = base_context;
                    context.seed = data.random.seed();
                    generator(data, context);
                    content = data.str();
                    if (!validator_ || validator_(content, id)) {
                        accepted = true;
                        break;
                    }
                }
                if (!accepted) {
                    throw std::runtime_error("validator rejected case " + stem +
                                             " too many times");
                }

                write_atomically(input, content);
                if (!options_.solution_command.empty()) {
                    run_solution(options_.solution_command, input, output, id);
                }
                ++result.generated;
                if (progress_) progress_(index + 1, total, input);
            } catch (const std::exception& exception) {
                result.ok = false;
                result.errors.push_back("case " + stem + ": " + exception.what());
            }
        }
        if (options_.emit_luogu_config) {
            try {
                write_luogu_config(total);
            } catch (const std::exception& exception) {
                result.ok = false;
                result.errors.push_back(std::string("config.yml: ") + exception.what());
            }
        }
        return result;
    }

private:
    PG_NODISCARD GenerationResult missing_generator() const {
        GenerationResult result;
        result.ok = false;
        result.seed = options_.seed;
        result.errors.emplace_back("no case generator was provided");
        return result;
    }

    PG_NODISCARD std::size_t total_case_count() const {
        if (options_.groups.empty()) return options_.case_count;
        std::size_t total = 0;
        for (const TestGroup& group : options_.groups) {
            if (group.count > std::numeric_limits<std::size_t>::max() - total) {
                throw std::overflow_error("test group case count overflows size_t");
            }
            total += group.count;
        }
        return total;
    }

    PG_NODISCARD GenerationContext context_for(
        std::size_t index, std::size_t total) const {
        GenerationContext context;
        context.id = options_.first_id + static_cast<i64>(index);
        context.index = index;
        context.count = total;
        context.phase = total > 1 ? static_cast<double>(index) / (total - 1) : 0.5;
        if (options_.groups.empty()) return context;
        std::size_t offset = 0;
        for (const TestGroup& group : options_.groups) {
            if (index < offset + group.count) {
                context.group = group.name;
                context.profile = group.profile;
                context.group_index = index - offset;
                context.group_count = group.count;
                context.group_phase = group.count > 1
                    ? static_cast<double>(context.group_index) / (group.count - 1)
                    : 0.5;
                return context;
            }
            offset += group.count;
        }
        throw std::out_of_range("case index is outside test groups");
    }

    PG_NODISCARD const TestGroup* group_for(std::size_t index) const {
        std::size_t offset = 0;
        for (const TestGroup& group : options_.groups) {
            if (index < offset + group.count) return &group;
            offset += group.count;
        }
        return nullptr;
    }

    void write_luogu_config(std::size_t total) const {
        if (options_.groups.empty()) return;
        std::ostringstream content;
        for (std::size_t index = 0; index < total; ++index) {
            const TestGroup* group = group_for(index);
            if (!group) continue;
            const i64 id = options_.first_id + static_cast<i64>(index);
            content << options_.file_prefix << id << ".in:\n";
            if (group->time_limit_ms >= 0) {
                content << "  timeLimit: " << group->time_limit_ms << '\n';
            }
            if (group->memory_limit_kb >= 0) {
                content << "  memoryLimit: " << group->memory_limit_kb << '\n';
            }
            if (group->score >= 0) content << "  score: " << group->score << '\n';
            if (group->pretest) content << "  isPretest: true\n";
            if (group->subtask_id >= 0) {
                content << "  subtaskId: " << group->subtask_id << '\n';
            }
        }
        write_atomically(detail::join_path(options_.output_directory, "config.yml"),
                         content.str());
    }

    static void write_atomically(const std::string& destination,
                                 const std::string& content) {
        const std::string temporary = destination + ".tmp";
        {
            std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
            if (!stream) throw std::runtime_error("cannot open " + temporary);
            stream.write(content.data(), static_cast<std::streamsize>(content.size()));
            if (!stream) throw std::runtime_error("cannot write " + temporary);
        }
        if (!detail::replace_file(temporary, destination)) {
            std::remove(temporary.c_str());
            throw std::runtime_error("cannot finalize file: " + destination);
        }
    }

    static void run_solution(std::string command, const std::string& input,
                             const std::string& output, i64 id) {
        const std::string temporary = output + ".tmp";
        const bool has_input = command.find("{input}") != std::string::npos;
        const bool has_output = command.find("{output}") != std::string::npos;
        replace_all(command, "{input}", shell_quote(input));
        replace_all(command, "{output}", shell_quote(temporary));
        replace_all(command, "{id}", std::to_string(id));
        if (!has_input) command += " < " + shell_quote(input);
        if (!has_output) command += " > " + shell_quote(temporary);
        if (std::system(command.c_str()) != 0) {
            throw std::runtime_error("solution command failed");
        }
        if (!detail::replace_file(temporary, output)) {
            std::remove(temporary.c_str());
            throw std::runtime_error("cannot finalize output: " + output);
        }
    }

    ProblemOptions options_;
    Validator validator_;
    Progress progress_;
};

}  // namespace pg

#undef PG_NODISCARD

#endif  // DATA_GENERATOR_RANDOM_H
