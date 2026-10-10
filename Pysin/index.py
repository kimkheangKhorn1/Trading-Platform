# This is the casting concept in python programming language
x = "3"
y = int(x) 
print(type(y))

a = "       Strip method will remove all the white space at the beginning and the end of the string in python programming language.    "
print(a.strip())
# String a will be converted to upper case and the lower case using upper() and lower() methods in python programming language.
print(a.upper())
print(a.lower())



# string will be used to fomat the string in python programming language. 
z =y = a = "Assigning multiple variable in pythong programming language."
print(z)
print(y)
print(a)
print(type(z))
print(type(y))
print(type(a))


ab, bc, cd = "This is ab", "This is bc", "This is cd"
print (ab)
print(bc)
print(cd) 


x="String A "
y="String B " 
z="String C "

abc=x+y+z
print(abc)


def my_function():
    global x
    x="This is the global variable"
    print(x)
my_function()

print("This is the global variable x = "+ x)


variable_a=23
variable_b=25
variable_c=50
#logical operator 
if variable_a < variable_b and variable_b<variable_c:
    print("23<25 and 25<50")
elif variable_a < variable_b or variable_b>variable_c:
    print("23<25 or 25<50")
else:
    print("All condition is incorrect, please check again to confirm that everything is incorrect")

# Python operator in python programming language +, -, *, \\, %, 
# Arimatics operators
variable_1 = 14
variable_2 = 15
variable_3 = 34

operation1 = variable_1 + variable_2
operator2 = variable_2 - variable_3
operator3 = variable_3 * variable_3
operator4 = variable_1 // variable_1
operator = variable_2 % variable_1

# Ternary operator in pythong programming language
